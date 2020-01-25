import os
import pickle
from functools import lru_cache
from datetime import datetime
from torchvision.utils import make_grid
import torchvision.models as models
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import numpy as np
from scipy.io import loadmat
import matplotlib.pyplot as plt
import argparse
from pathlib import Path
from sklearn.metrics import confusion_matrix

parser = argparse.ArgumentParser()
parser.add_argument('--batch_size', default=32, type=int)
parser.add_argument('--datadir', default='data700', type=str)
parser.add_argument('--img_size', default=28, type=int)
args = parser.parse_args()

np.random.seed(0)
torch.manual_seed(0)
torch.backends.cudnn.deterministic = True
torch.backends.cudnn.benchmark = True
device = 'cuda' if torch.cuda.is_available() else 'cpu'


class MNISTNet(nn.Module):
    def __init__(self):
        super(MNISTNet, self).__init__()
        self.conv1 = nn.Conv2d(3, 32, 3, 1)
        self.conv2 = nn.Conv2d(32, 64, 3, 1)
        self.dropout1 = nn.Dropout2d(0.25)
        self.dropout2 = nn.Dropout2d(0.5)
        self.fc1 = nn.Linear(9216, 128)
        self.fc2 = nn.Linear(128, 10)

    def forward(self, x):
        x = self.conv1(x)
        x = F.relu(x)
        x = self.conv2(x)
        x = F.max_pool2d(x, 2)
        x = self.dropout1(x)
        x = torch.flatten(x, 1)
        x = self.fc1(x)
        x = F.relu(x)
        x = self.dropout2(x)
        x = self.fc2(x)
        return x


@lru_cache()
def load_data():
    cache_file = os.path.join(args.datadir, 'customizedAffNIST.npz')
    if os.path.exists(cache_file):
        data = np.load(cache_file)
        return (data['x_train'], data['y_train']), (data['x_test'], data['y_test'])

    x_train, y_train, x_test, y_test = [], [], [], []
    for split in ['training', 'testing']:
        for classidx in range(10):
            datafile = os.path.join(args.datadir, '{}/dataORG_{}.mat'.format(split, classidx))
            data = loadmat(datafile)['xxO'].transpose([2, 0, 1])
            label = np.zeros(data.shape[0], dtype=np.int64)+classidx
            if split == 'training':
                x_train.append(data)
                y_train.append(label)
            else:
                x_test.append(data)
                y_test.append(label)
    x_train, y_train = np.concatenate(x_train), np.concatenate(y_train)
    x_test, y_test = np.concatenate(x_test), np.concatenate(y_test)
    print('input load data, x_train.shape {}, x_test.shape {}'.format(x_train.shape, x_test.shape))

    x_train = x_train / x_train.max(axis=(1, 2), keepdims=True)
    x_test = x_test / x_test.max(axis=(1, 2), keepdims=True)

    x_train = (x_train * 255.).astype(np.uint8)
    x_test = (x_test * 255.).astype(np.uint8)

    np.savez(cache_file, x_train=x_train, x_test=x_test, y_train=y_train, y_test=y_test)

    return (x_train, y_train), (x_test, y_test)

def take_samples(data, index):
    data_reshape = data.reshape(10, -1, 3, args.img_size, args.img_size)
    sub = np.take(data_reshape, index, axis=1)
    return sub.reshape(-1, 3, args.img_size, args.img_size)

if __name__ == '__main__':
    (x_train, y_train), (x_test, y_test) = load_data()
    # x_train shape: (class_idx*n_samples, args.img_size, args.img_size)
    x_train = (x_train.astype(np.float32) / 255. - 0.5) / 0.5
    x_train_format = np.repeat(x_train.reshape(-1, 1, args.img_size, args.img_size), axis=1, repeats=3)

    x_test = (x_test.astype(np.float32) / 255. - 0.5) / 0.5
    x_test_format = np.repeat(x_test.reshape(-1, 1, args.img_size, args.img_size), axis=1, repeats=3)
    x_test_format = torch.from_numpy(x_test_format).to(device)

    indices = loadmat(os.path.join(args.datadir, 'Ind_tr.mat'))['indtr'] - 1 # index start from 1
    val_indices = indices[4096:, 0]
    x_val = take_samples(x_train_format, val_indices)
    x_val = torch.from_numpy(x_val).to(device)
    y_val = np.repeat(np.arange(10), val_indices.shape[0])
    print('x_val.shape {}'.format(x_val.shape))

    # model = models.vgg11_bn(num_classes=10).to(device)
    model = MNISTNet().to(device)
    torch.save(model.state_dict(), './model_init.pth')

    for n_samples in [2**i for i in range(13)]:
    # for n_samples in [500]:
        for run in range(5):
            print('============== num samples {} run {} ============'.format(n_samples, run))
            model.load_state_dict(torch.load('./model_init.pth'))

            sub_index = indices[:n_samples, run]
            x_train_sub = take_samples(x_train_format, sub_index)
            y_train_sub = np.repeat(np.arange(10), n_samples)

            criterion = nn.CrossEntropyLoss()
            # optimizer = optim.SGD(model.parameters(), lr=1e-3, momentum=0.9, weight_decay=5e-4)
            optimizer = optim.Adam(model.parameters(), lr=5e-4)

            save_path = 'results/{}/samples-{}-model-{}/'.format(args.datadir, n_samples, type(model).__name__)
            Path(save_path).mkdir(parents=True, exist_ok=True)
            ckpt_path = os.path.join(save_path, 'run{}.pkl'.format(run))
            best_val_acc = 0.0

            for epoch in range(50):
                perm = np.random.permutation(x_train_sub.shape[0])
                x_train_sub_perm = x_train_sub[perm]
                y_train_sub_perm = y_train_sub[perm]

                # train
                for i in range(0, x_train_sub_perm.shape[0], args.batch_size):
                    inputs = x_train_sub_perm[i: i + args.batch_size]
                    targets = y_train_sub_perm[i: i + args.batch_size]
                    inputs = torch.from_numpy(inputs).to(device)
                    targets = torch.from_numpy(targets).to(device)
                    # if inputs.shape[0] != args.batch_size:
                    #     break

                    optimizer.zero_grad()
                    outputs = model(inputs)
                    loss = criterion(outputs, targets)
                    loss.backward()
                    optimizer.step()

                    _, max_indices = torch.max(outputs, 1)

                    train_acc = (max_indices == targets).type(torch.float).mean()
                    if (i//args.batch_size) // 10 == 0:
                        print('epoch {} iter {} train loss {:.5f} acc {:.5f}'.format(epoch, i//args.batch_size, loss.item(), train_acc))

                # validation
                with torch.no_grad():
                    val_logits = []
                    for i in range(0, x_val.shape[0], 2000):
                      batch_logit = model(x_val[i:i+2000])
                      val_logits.append(batch_logit.cpu().numpy())
                    val_logits = np.concatenate(val_logits)
                    val_acc = (np.argmax(val_logits, axis=1) == y_val).mean()
                    print('epoch {} val acc {:.5f}'.format(epoch, val_acc))
                    if val_acc > best_val_acc:
                        best_val_acc = val_acc
                        state = dict(model=model.state_dict(), best_val_acc=val_acc, epoch=epoch)
                        torch.save(state, ckpt_path)
                        print('saved to ' + ckpt_path)
            # test
            with torch.no_grad():
                state = torch.load(ckpt_path)
                model.load_state_dict(state['model'])
                print('recovered from {}'.format(ckpt_path))
                print('samples {} run {} best val acc {}, epoch {}'.format(n_samples, run, state['best_val_acc'],
                                                                           state['epoch']), end=' ')
                logit = []
                for i in range(0, x_test_format.shape[0], 2000):
                  test_logit = model(x_test_format[i:i+2000])
                  logit.append(test_logit.cpu().numpy())
                logit = np.concatenate(logit)
                y_pred = np.argmax(logit, axis=1)
                test_acc = (y_pred == y_test).mean()
                del state['model']
                state['test_acc'] = test_acc
                state['confusion_matrix'] = confusion_matrix(y_test, y_pred)
                print('test acc {:.5f}'.format(test_acc))
                print(state['confusion_matrix'])
                with open(ckpt_path, 'wb') as f:
                    pickle.dump(state, f)
                # # to load the data
                # with open(ckpt_path, 'rb') as f:
                #     state = pickle.load(f)

                print('saved to {}'.format(ckpt_path))


    # plt.imshow(make_grid(torch.from_numpy(x_train[:16]).view(-1, 1, args.img_size, args.img_size), pad_value=1).permute(1, 2, 0))
    # plt.savefig('out.png')
    # plt.show()

