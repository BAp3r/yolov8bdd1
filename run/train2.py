import os
import yaml
import json
import math
import torch
import argparse
from torch import nn
from tqdm import tqdm
from copy import deepcopy
from torch.cuda import amp
from tools import load_model
from torch.optim import lr_scheduler
from torch.utils.data import DataLoader
from plot import plot_images, plot_labels
from dataset import build_labels, LoadDataset
from torch.optim.lr_scheduler import CosineAnnealingLR


class Optim:
    def __init__(self):
        pass

    def build_optimizer(self, model, batch_size, num_batch_size, decay=0.0005,
                        optim='Adam', lr=0.01, momentum=0.937, weight_path=''):
        accumulate = max(round(num_batch_size / batch_size), 1)
        decay = decay * batch_size * accumulate / num_batch_size
        params = [[], [], []]
        for v in model.modules():
            if hasattr(v, 'bias') and isinstance(v.bias, nn.Parameter):
                params[2].append(v.bias)
            if hasattr(v, 'weight') and isinstance(v, nn.BatchNorm2d):
                params[1].append(v.weight)
            elif hasattr(v, 'weight') and isinstance(v.weight, nn.Parameter):
                params[0].append(v.weight)

        if optim == 'Adam':
            optimizer = torch.optim.Adam(params[2], lr=lr, betas=(momentum, 0.999))
        elif optim == 'AdamW':
            optimizer = torch.optim.AdamW(params[2], lr=lr, betas=(momentum, 0.999))
        elif optim == 'RMSProp':
            optimizer = torch.optim.RMSprop(params[2], lr=lr, momentum=momentum)
        elif optim == 'SGD':
            optimizer = torch.optim.SGD(params[2], lr=lr, momentum=momentum, nesterov=True)
        else:
            raise NotImplementedError(f'Optimizer {optim} not implemented.')

        optimizer.add_param_group({'params': params[0], 'weight_decay': decay})
        optimizer.add_param_group({'params': params[1], 'weight_decay': 0.0})

        if weight_path:
            optimizer.load_state_dict(torch.load(weight_path))

        return optimizer

    def build_scheduler(self, optimizer, epochs, one_cycle=False, lrf=0.01, start_epoch=0):
        if one_cycle:
            lf = lambda x: ((1 - math.cos(x * math.pi / epochs)) / 2) * (lrf - 1) + 1
        else:
            lf = lambda x: (1 - x / epochs) * (1.0 - lrf) + lrf  # linear

        scheduler = lr_scheduler.LambdaLR(optimizer, lr_lambda=lf)
        scheduler.last_epoch = start_epoch - 1

        return scheduler


class EMA:  # exponential moving average
    def __int__(self, model, decay=0.9999, tau=2000, updates=0, weight_path=''):
        self.model = deepcopy(model).eval()
        for param in self.model.parameters():
            param.requires_grad_(False)

        if weight_path:
            self.model.load_state_dict(torch.load(weight_path)['model'])

        self.decay_fun = lambda x: decay * (1 - math.exp(-x / tau))
        self.updates = updates

    def update(self, model):
        self.updates += 1
        decay = self.decay_fun(self.updates)

        for k, v in self.model.state_dict().items():
            if v.dtype.is_floating_point:
                v = v * decay + (1 - decay) * model.state_dict()[k].detach()


class EarlyStop:
    def __init__(self, best_epoch=0, best_fitness=0.0, patience=50):
        self.best_epoch = best_epoch
        self.best_fitness = best_fitness
        self.patience = patience or float('inf')

    def __call__(self, epoch, fitness):
        if fitness >= self.best_fitness:
            self.best_epoch = epoch
            self.best_fitness = fitness

        stop = epoch - self.best_epoch >= self.patience

        if stop:
            print(f'stop training early at {epoch}th epoch, the best one is {self.best_epoch}th epoch')

        return stop


class Train:
    def __int__(self, args, hyp, device):
        self.args = args
        self.hyp = hyp
        self.device = device

        # resume train
        if args.resume_log_dir:
            self.model_weight_path = os.path.join(args.resume_log_dir, 'weight', 'model.pth')
            self.optim_weight_path = os.path.join(args.resume_log_dir, 'weight', 'optim.pth')
            self.ema_weight_path = os.path.join(args.resume_log_dir, 'weight', 'ema.pth')

            param_path = os.path.join(args.resume_log_dir, 'param.json')
            with open(param_path) as f:
                resume_param = json.load(f)
                self.start_epoch = resume_param['epoch']
                self.updates = resume_param['updates']
                self.best_epoch = resume_param['best_epoch']
                self.best_fitness = resume_param['best_fitness']

        else:
            self.model_weight_path = self.args.weight_path
            self.optim_weight_path = ''
            self.ema_weight_path = ''
            self.start_epoch = 0
            self.updates = 0
            self.best_epoch = 0
            self.best_fitness = 0.0

    def setup_train(self):
        # model
        self.model = load_model(self.args.model_path, self.args.training, self.args.fused, self.model_weight_path)
        self.model.to(self.device)

        # optimizer
        self.optimizer = build_optimizer(self.model, self.args.batch_size, self.args.num_batch_size, self.args.decay,
                                         self.args.optim, self.args.lr, self.args.momentum, self.optim_weight_path)

        # scheduler
        self.scheduler = build_scheduler(self.optimizer, self.args.one_cycle,
                                         self.args.epochs, self.args.lrf, self.start_epoch)

        # ema
        self.ema = EMA(self.model, self.hyp['decay'], self.hyp['tau'], self.updates, self.ema_weight_path)

        # early stop
        self.stopper, self.stop = EarlyStop(self.best_epoch, self.best_fitness, self.args.patience), False

        # dataset
        train_dataset = LoadDataset(self.args.train_img_dir, self.args.train_label_file, self.hyp)
        self.train_dataloader = DataLoader(dataset=train_dataset, batch_size=self.args.batch_size,
                                           num_workers=self.args.njobs, shuffle=True, collate_fn=LoadDataset.collate_fn)

    def exec_train(self):
        self.setup_train()


parser = argparse.ArgumentParser()
parser.add_argument('--train_img_dir', type=str, default='../dataset/bdd100k/images/train')
parser.add_argument('--train_label_file', type=str, default='../dataset/bdd100k/labels/train.txt')
parser.add_argument('--val_img_dir', type=str, default='../dataset/bdd100k/images/val')
parser.add_argument('--val_label_file', type=str, default='../dataset/bdd100k/labels/val.txt')
parser.add_argument('--cls_file', type=str, default='../dataset/bdd100k/cls.yaml')

parser.add_argument('--hyp_file', type=str, default='../config/hyp/hyp.yaml')
parser.add_argument('--model_file', type=str, default='../config/model/yolov8x.yaml')
parser.add_argument('--weight_file', type=str, default='../config/weight/yolov8x.pth')
parser.add_argument('--training', type=bool, default=True)
parser.add_argument('--fused', type=bool, default=True)

parser.add_argument('--pretrain_dir', type=str, default='')
parser.add_argument('--log_dir', type=str, default='../log/train')
parser.add_argument('--batch_size', type=str, default=2)
parser.add_argument('--njobs', type=str, default=1)
args = parser.parse_args()

if __name__ == "__main__":
    # build label
    if not os.path.exists(args.train_label_file) or not os.path.exists(args.val_label_file):
        print('build yolo labels')
        cls = yaml.safe_load(open('../dataset/bdd100k/cls.yaml', encoding="utf-8"))
        build_labels('../dataset/bdd100k/labels/train.json',
                     args.train_label_file, args.train_img_dir, cls)
        build_labels('../dataset/bdd100k/labels/val.json',
                     args.val_label_file, args.val_img_dir, cls)

    train(args)
