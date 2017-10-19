#!/usr/bin/python
# -*- coding: UTF-8 -*-

import matplotlib
matplotlib.use('Agg')
from numpy import random

# Pytorch requirements
import unicodedata
import string
import re
import random

import torch
import torch.nn as nn
from torch.nn import init
from torch.autograd import Variable
from torch import optim
import torch.nn.functional as F

if torch.cuda.is_available():
    dtype = torch.cuda.FloatTensor
    dtype_l = torch.cuda.LongTensor
else:
    dtype = torch.FloatTensor
    dtype_l = torch.cuda.LongTensor

def sinkhorn_knopp(A, iterations=1):
    A_size = A.size()
    for it in range(iterations):
        A = A.view(A_size[0]*A_size[1], A_size[2])
        A = F.softmax(A)
        A = A.view(*A_size).permute(0, 2, 1)
        A = A.view(A_size[0]*A_size[1], A_size[2])
        A = F.softmax(A)
        A = A.view(*A_size).permute(0, 2, 1)
    return A

def gmul(input):
    W, x, y = input
    # x is a tensor of size (bs, N, num_features)
    # y is a tensor of size (bs, N, N)
    # W is a tensor of size (bs, N, N, J)
    x_size = x.size()
    W_size = W.size()
    N = W_size[-2]
    J = W_size[-1]
    W = W.split(1, 3)
    W = W + (y.unsqueeze(3),)
    W = torch.cat(W, 1).squeeze(3) # W is now a tensor of size (bs, J*N, N)
    output = torch.bmm(W, x) # output has size (bs, J*N, num_features)
    output = output.split(N, 1)
    output = torch.cat(output, 2) # output has size (bs, N, J*num_features)
    return output

def normalize_embeddings(emb):
    norm = torch.mul(emb, emb).sum(2).unsqueeze(2).sqrt().expand_as(emb)
    return emb.div(norm)

class Gconv_last(nn.Module):
    def __init__(self, feature_maps, J):
        super(Gconv_last, self).__init__()
        self.num_inputs = (J+1)*feature_maps[0]
        self.num_outputs = feature_maps[2]
        self.fc = nn.Linear(self.num_inputs, self.num_outputs)
        self.beta = nn.Linear(self.num_outputs, 1, bias=True)
        self.sigma = nn.Parameter(torch.Tensor([random.uniform(0.0,1.0)]).type(dtype))

    def forward(self, input):
        W, x, y = input
        N = y.size(-1)
        bs = y.size(0)
        x = gmul(input) # out has size (bs, N, num_inputs)
        x_size = x.size()
        x = x.contiguous()
        x = x.view(x_size[0]*x_size[1], -1)
        x = self.fc(x) # has size (bs*N, num_outputs)
        x = x.view(*x_size[:-1], self.num_outputs)
        bx = self.beta(x)
        Bx = bx.expand(bs,N,N)
        y = F.sigmoid(Bx + Bx.permute(0,2,1) + self.sigma*y)
        #y = (y + y.permute(0,2,1))/2
        y = y * (1-Variable(torch.eye(N).type(dtype)).unsqueeze(0).expand(bs,N,N))
        return W, x, y

class Gconv(nn.Module):
    def __init__(self, feature_maps, J):
        super(Gconv, self).__init__()
        self.num_inputs = (J+1)*feature_maps[0]
        self.num_outputs = feature_maps[2]
        self.fc1 = nn.Linear(self.num_inputs, self.num_outputs // 2)
        self.fc2 = nn.Linear(self.num_inputs, self.num_outputs // 2)
        self.beta = nn.Linear(self.num_outputs, 1, bias=True)
        self.sigma = nn.Parameter(torch.Tensor([random.uniform(0.0,1.0)]).type(dtype))
        self.bn = nn.BatchNorm1d(self.num_outputs)
        self.bn_instance = nn.InstanceNorm1d(self.num_outputs)

    def forward(self, input):
        W, x, y = input
        N = y.size(-1)
        bs = y.size(0)
        x = gmul(input) # out has size (bs, N, num_inputs)
        x_size = x.size()
        x = x.contiguous()
        x = x.view(-1, self.num_inputs)
        x1 = F.relu(self.fc1(x)) # has size (bs*N, num_outputs)
        x2 = self.fc2(x)
        x = torch.cat((x1, x2), 1)
        x = x.view(*x_size[:-1], self.num_outputs)
        x = self.bn_instance(x.permute(0, 2, 1)).permute(0, 2, 1)
        bx = self.beta(x)
        Bx = bx.expand(bs,N,N) # Bx has size (bs, N, N)
        y = F.sigmoid(Bx + Bx.permute(0,2,1) + self.sigma*y) # if y was symetric will remains symetric
        #y = (y + y.permute(0,2,1))/2
        y = y * (1-Variable(torch.eye(N).type(dtype)).unsqueeze(0).expand(bs,N,N))
        return W, x, y

class GNN(nn.Module):
    def __init__(self, num_features, num_layers, J, dim_input=1):
        super(GNN, self).__init__()
        self.num_features = num_features
        self.num_layers = num_layers
        self.featuremap_in = [dim_input, 1, num_features]
        self.featuremap_mi = [num_features, num_features, num_features]
        self.featuremap_end = [num_features, num_features, num_features]
        self.layer0 = Gconv(self.featuremap_in, J)
        for i in range(num_layers):
            module = Gconv(self.featuremap_mi, J)
            self.add_module('layer{}'.format(i + 1), module)
        self.layerlast = Gconv_last(self.featuremap_end, J)

    def forward(self, input):
        cur = self.layer0(input)
        for i in range(self.num_layers):
            cur = self._modules['layer{}'.format(i+1)](cur)
        out = self.layerlast(cur)
        return out[1]

class Siamese_GNN(nn.Module):
    def __init__(self, num_features, num_layers, N, J,
                 dim_input=1, dual=False):
        super(Siamese_GNN, self).__init__()
        self.N = N
        self.dual = dual
        if self.dual:
            dim_input=1
        self.gnn = GNN(num_features, num_layers, J, dim_input=dim_input)
        self.linear_dual = nn.Linear(num_features, 1)

    def forward(self, g1, g2):
        emb1 = self.gnn(g1)
        # embx are tensors of size (bs, N, num_features)
        if self.dual:
            emb_size = emb1.size()
            emb1 = emb1.view(-1, emb_size[-1])
            emb1 = self.linear_dual(emb1)
            emb1 = emb1.view(*emb_size[:2], 1)
            # reshape edge embeddings
            batch_size = emb1.size()[0]
            out = Variable(torch.zeros(batch_size, self.N, self.N)).type(dtype)
            for b in range(batch_size):
                count = 0
                for i in range(0, self.N-1):
                    for j in range(i+1, self.N):
                        # print(i, j)
                        out[b, i, j] = emb1[b, count]
                        count +=1
        else:
            # l2normalize the embeddings
            emb1 = normalize_embeddings(emb1)
            out = torch.bmm(emb1, emb1.permute(0, 2, 1))
            diag = (-1000 * Variable(torch.eye(self.N).unsqueeze(0)
                    .expand_as(out)).type(dtype))
            # print('out', out[0])
            out = out + diag
            # print('out', out[0])
        return out # out has size (bs, N, N)

class Siamese_2GNN(nn.Module):
    def __init__(self, num_features, num_layers, J, dim_input=1):
        super(Siamese_2GNN, self).__init__()
        self.gnn1 = GNN(num_features, num_layers, J, dim_input=dim_input)
        self.gnn2 = GNN(num_features, num_layers, J, dim_input=dim_input)

    def forward(self, g1, g2):
        emb1 = self.gnn1(g1)
        emb2 = self.gnn2(g2)
        # embx are tensors of size (bs, N, num_features)
        out = torch.bmm(emb1, emb2.permute(0, 2, 1))
        return out # out has size (bs, N, N)

if __name__ == '__main__':
    # test modules
    bs =  4
    num_features = 10
    num_layers = 5
    N = 8
    x = torch.ones((bs, N, num_features))
    W1 = torch.eye(N).unsqueeze(0).unsqueeze(-1).expand(bs, N, N, 1)
    W2 = torch.ones(N).unsqueeze(0).unsqueeze(-1).expand(bs, N, N, 1)
    J = 2
    W = torch.cat((W1, W2), 3)
    input = [Variable(W), Variable(x)]
    ######################### test gmul ##############################
    # feature_maps = [num_features, num_features, num_features]
    # out = gmul(input)
    # print(out[0, :, num_features:])
    ######################### test gconv ##############################
    # feature_maps = [num_features, num_features, num_features]
    # gconv = Gconv(feature_maps, J)
    # _, out = gconv(input)
    # print(out.size())
    ######################### test gnn ##############################
    # x = torch.ones((bs, N, 1))
    # input = [Variable(W), Variable(x)]
    # gnn = GNN(num_features, num_layers, J)
    # out = gnn(input)
    # print(out.size())
    ######################### test siamese gnn ##############################
    x = torch.ones((bs, N, 1))
    input1 = [Variable(W), Variable(x)]
    input2 = [Variable(W.clone()), Variable(x.clone())]
    siamese_gnn = Siamese_GNN(num_features, num_layers, J)
    out = siamese_gnn(input1, input2)
    print(out.size())


