#!/usr/bin/python
# -*- coding: UTF-8 -*-

import sys
import numpy as np
import os
# import dependencies
import time
from LKH.tsp_solver import TSP
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt

#Pytorch requirements
import unicodedata
import string
import re
import random
import argparse
import math

import torch
import torch.nn as nn
from torch.nn import init
from torch.autograd import Variable
from torch import optim
import torch.nn.functional as F

if torch.cuda.is_available():
    dtype = torch.cuda.FloatTensor
    dtype_l = torch.cuda.LongTensor
    torch.cuda.manual_seed(0)
else:
    dtype = torch.FloatTensor
    dtype_l = torch.LongTensor
    torch.manual_seed(0)

class Generator(TSP):
    def __init__(self, path_dataset, path_tsp, mode='CEIL_2D'):
        super().__init__(path_tsp)
        # TSP.__init__(self, path_dataset)
        self.path_dataset = path_dataset
        self.num_examples_train = 10e6
        self.num_examples_test = 10e4
        self.data_train = []
        self.data_test = []
        self.dual = False
        self.N = 20
        self.J = 4
        self.mode = mode
        self.sym = True

    def ErdosRenyi(self, p, N):
        W = np.zeros((N, N))
        for i in range(0, N - 1):
            for j in range(i + 1, N):
                add_edge = (np.random.uniform(0, 1) < p)
                if add_edge:
                    W[i, j] = 1
                W[j, i] = W[i, j]
        return W

    def compute_operators(self, W):
        # operators: {Id, W, W^2, ..., W^{J-1}, D, U}
        N = W.shape[0]
        d = W.sum(1)
        D = np.diag(d)
        QQ = W.copy()
        WW = np.zeros([N, N, self.J + 2])
        WW[:, :, 0] = np.eye(N)
        for j in range(self.J):
            WW[:, :, j + 1] = QQ.copy()
            QQ = np.dot(QQ, QQ)
            QQ /= QQ.max()
            QQ *= np.sqrt(2)
        WW[:, :, self.J] = D
        WW[:, :, self.J + 1] = np.ones((N, N)) * 1.0 / float(N)
        WW = np.reshape(WW, [N, N, self.J + 2])
        x = np.reshape(d, [N, 1])
        return WW, x

    def adj_from_coord(self, cities):
        N = cities.shape[0]
        if self.dual:
            E =  int(N*(N-1)/2)
            Edges = []
            W = np.zeros((E, E))
            for i in range(0, N-1):
                for j in range(i+1,N):
                    Edges.append([i, j])
            assert len(Edges) == E
            Edges = np.array(Edges)
            for i in range(E):
                W[i] = ((Edges[i,0] == Edges) + (Edges[i,1] == Edges)).sum(1)
            # zero diagonal
            for i in range(E):
                W[i, i] = 0
        else:
            W = np.zeros((N, N))
            def l2_dist(x, y):
                return math.ceil(np.sqrt(np.square(x - y).sum()))
            def l1_dist(x, y):
                return np.abs(x - y).sum()
            for i in range(0, N - 1):
                for j in range(i + 1, N):
                    city1 = cities[i]*self.C
                    city2 = cities[j]*self.C
                    dist = l2_dist(city1, city2)/float(self.C)
                    W[i, j] = np.sqrt(2) - float(dist)
                    W[j, i] = W[i, j]
        return W

    def cycle_adj(self, N, sym=False):
        W = np.zeros((N, N))
        if sym:
            W[N-1, N-2] = 1
            W[N-1, 0] = 1
            W[0, 1] = 1
            W[0, N-1] = 1
            for i in range(1, N-1):
                W[i, i-1] = 1
                W[i, i+1] = 1
        else:
            W[N-1, N-2] = 0
            W[N-1, 0] = 0
            W[0, 1] = 1
            W[0, N-1] = 1
            for i in range(1, N-1):
                W[i, i-1] = 0
                W[i, i+1] = 1
        return W

    def create_dual_embeddings(self, cities):
        def l2_dist(x, y):
            return math.ceil(np.sqrt(np.square(x - y).sum()))
        def l1_dist(x, y):
            return np.abs(x - y).sum()
        x = []
        for i in range(0, self.N-1):
            for j in range(i+1, self.N):
                city1 = cities[i]*self.C
                city2 = cities[j]*self.C
                dist = l2_dist(city1, city2)/float(self.C)
                dist = np.sqrt(2) - float(dist)
                x.append(dist)
        x = np.reshape(np.array(x), [-1,1])
        return x

    def create_adj(self, Cities):
        cities = Cities.cpu().numpy()
        N = cities.shape[1]
        batch_size = cities.shape[0]
        W = np.zeros((batch_size, N, N))
        def l2_dist(x, y):
            return math.ceil(np.sqrt(np.square(x - y).sum()))
        def l1_dist(x, y):
            return np.abs(x - y).sum()
        for b in range(batch_size):
            for i in range(0, N - 1):
                for j in range(i + 1, N):
                    city1 = cities[b, i]*self.C
                    city2 = cities[b, j]*self.C
                    dist = l2_dist(city1, city2)/float(self.C)
                    W[b, i, j] = np.sqrt(2) - float(dist)
                    W[b, j, i] = W[b, i, j]
        W = torch.from_numpy(W).type(dtype)
        return W

    def compute_example(self, i):
        example = {}
        if self.mode == 'CEIL_2D':
            cities = self.cities_generator(self.N)
            if i == 0 and self.dual:
                W = self.adj_from_coord(cities)
                WW, x = self.compute_operators(W)
                example['WW'] = WW
            else:
                W = self.adj_from_coord(cities)
                WW, x = self.compute_operators(W)
                example['WW'] = WW
            # add_coordinates
            if self.dual:
                x = self.create_dual_embeddings(cities)
            else:
                x = np.concatenate([x, cities], axis=1)
            example['cities'] = cities
            example['x'] = x
            # compute hamiltonian cycle
            self.save_solverformat(cities, self.N, mode='CEIL_2D')
        elif self.mode == 'EXPLICIT':
            W = self.adj_generator(self.N)
            WW, x = self.compute_operators(W)
            example['WW'], example['x'] = WW, x
            # compute hamiltonian cycle
            self.save_solverformat(W, self.N, mode='EXPLICIT')
            raise ValueError('Mode {} not yet supported.'.format(mode))
        else:
            raise ValueError('Mode {} not supported.'.format(mode))
        self.tsp_solver(self.N)
        # print(cities)
        ham_cycle, length_cycle = self.extract_path(self.N)
        example['HAM_cycle'] = ham_cycle
        cost = float(length_cycle)/float(self.C)
        example['Length_cycle'] = np.sqrt(2)*self.N - cost
        example['WTSP'] = self.perm_to_adj(ham_cycle, self.N)
        example['labels'] = self.perm_to_labels(ham_cycle, self.N,
                                                sym=self.sym)
        example['perm'] = ham_cycle
        return example

    def create_dataset_train(self):
        for i in range(self.num_examples_train):
            example = self.compute_example(i)
            self.data_train.append(example)
            if i % 100 == 0:
                print('Train example {} of length {} computed.'
                      .format(i, self.N))

    def create_dataset_test(self):
        for i in range(self.num_examples_test):
            example = self.compute_example(i)
            self.data_test.append(example)
            if i % 100 == 0:
                print('Test example {} of length {} computed.'
                      .format(i, self.N))

    def load_dataset(self):
        # load train dataset
        filename = 'TSP{}{}train_dual_{}.np'.format(self.N, self.mode,
                                                    self.dual)
        path = os.path.join(self.path_dataset, filename)
        if os.path.exists(path):
            print('Reading training dataset at {}'.format(path))
            self.data_train = np.load(open(path, 'rb'))
        else:
            print('Creating training dataset.')
            self.create_dataset_train()
            print('Saving training datatset at {}'.format(path))
            np.save(open(path, 'wb'), self.data_train)
        # load test dataset
        filename = 'TSP{}{}test_dual_{}.np'.format(self.N, self.mode,
                                                   self.dual)
        path = os.path.join(self.path_dataset, filename)
        if os.path.exists(path):
            print('Reading testing dataset at {}'.format(path))
            self.data_test = np.load(open(path, 'rb'))
        else:
            print('Creating testing dataset.')
            self.create_dataset_test()
            print('Saving testing datatset at {}'.format(path))
            np.save(open(path, 'wb'), self.data_test)

    def sample_batch(self, num_samples, is_training=True, it=0,
                     cuda=True, volatile=False):
        WW_size = self.data_train[0]['WW'].shape
        x_size = self.data_train[0]['x'].shape

        # define batch elements
        WW = torch.zeros(num_samples, *WW_size)
        X = torch.zeros(num_samples, *x_size)
        Y = torch.zeros(num_samples, self.N, self.N)
        WTSP = torch.zeros(num_samples, self.N, self.N)
        if self.sym:
            P = torch.zeros(num_samples, self.N, 2)
        else:
            P = torch.zeros(num_samples, self.N)
        Cities = torch.zeros((num_samples, self.N, 2))
        Perm = torch.zeros((num_samples, self.N))
        Cost = np.zeros(num_samples)
        # fill batch elements 
        if is_training:
            dataset = self.data_train
        else:
            dataset = self.data_test
        for b in range(num_samples):
            if is_training:
                # random element in the dataset
                ind = np.random.randint(0, len(dataset))
            else:
                ind = it * num_samples + b
            if self.dual:
                ww = torch.from_numpy(dataset[0]['WW'])
            else:
                ww = torch.from_numpy(dataset[ind]['WW'])
            x = torch.from_numpy(dataset[ind]['x'])
            WW[b], X[b] = ww, x
            Y[b] = ww[:,:,1]
            WTSP[b] = torch.from_numpy(dataset[ind]['WTSP'])
            P[b] = torch.from_numpy(dataset[ind]['labels'])
            Cities[b] = torch.from_numpy(dataset[ind]['cities'])
            Perm[b] = torch.from_numpy(dataset[ind]['perm'])
            Cost[b] = dataset[ind]['Length_cycle']
        # wrap as variables
        WW = Variable(WW, volatile=volatile)
        X = Variable(X, volatile=volatile)
        Y = Variable(Y, volatile=volatile)
        WTSP = Variable(WTSP, volatile=volatile)
        P = Variable(P, volatile=volatile)
        if cuda:
            return ([WW.cuda(), X.cuda(), Y.cuda()], [WTSP.cuda(), P.cuda()],
                    Cities.cuda(), Perm.cuda(), Cost)
        else:
            return [WW, X, Y], [WTSP, P], Cities, Perm, Cost

if __name__ == '__main__':
    # Test Generator module
    path_dataset = '/data/anowak/TSP/'
    path_tsp = '/home/anowak/QAP_pt/src/tsp/LKH/'
    gen = Generator(path_dataset, path_tsp)
    # N = 20
    # gen.num_examples_train = 20000
    # gen.num_examples_test = 1000
    # gen.N = N
    # gen.load_dataset()
    # out = gen.sample_batch(32, cuda=False)
    # W = out[0][0][0, :, :, 1]
    # W2 = out[0][0][0, :, :, 2]
    # W3 = out[0][0][0, :, :, 3]
    # print(W, W2, W3)
    # # print(g1[0].size())
    # # print(g1[0][0].data.cpu().numpy())
    # print('Dataset of length {} created.'.format(N))
    ########################## test dual ######################################
    gen.N = 50
    gen.dual = False
    gen.num_examples_train = 20000
    gen.num_examples_test = 1000
    W = gen.cities_generator(gen.N)
    W_dual = gen.adj_from_coord(W)
    print('W_dual', W_dual.shape)
    gen.load_dataset()
    A = gen.sample_batch(8, cuda=False)


