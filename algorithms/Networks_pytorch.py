import torch.nn.functional as F
from torch import nn
import torch.nn as nn
from torch.nn.utils import weight_norm
from torch.autograd import Variable
import torch
import numpy as np
import torch.nn as nn
import torch.optim as optim
from sklearn.metrics import mean_squared_error, mean_absolute_error
from sklearn.metrics import mean_squared_error, mean_absolute_error, f1_score, recall_score, precision_score
from sklearn.utils import shuffle
import math

# these 2 functions are used to rightly convert the dataset for LSTM prediction
class FPLSTMDataset(torch.utils.data.Dataset):
    def __init__(self, x, y):
        # swap axes to have timesteps before features
        self.x_tensors = {i : torch.as_tensor(np.swapaxes(x[i,:,:], 0, 1),
            dtype=torch.float32) for i in range(x.shape[0])}
        self.y_tensors = {i : torch.as_tensor(y[i], dtype=torch.int64)
                for i in range(y.shape[0])}

    def __len__(self):
        return len(self.x_tensors.keys())

    def __getitem__(self, idx):
        return (self.x_tensors[idx], self.y_tensors[idx])

def FPLSTM_collate(batch):
    xx, yy = zip(*batch)
    x_batch = torch.stack(xx).permute(1, 0, 2)
    y_batch = torch.stack(yy)
    return (x_batch, y_batch)

# fault prediction LSTM: this network is used as a reference in the paper
class FPLSTM(nn.Module):

    def __init__(self, lstm_size, fc1_size, input_size, n_classes,
            dropout_prob):
        super(FPLSTM, self).__init__()
        self.lstm_size = lstm_size
        self.lstm = nn.LSTM(input_size, lstm_size)
        self.do1  = nn.Dropout(dropout_prob)
        self.fc1 = nn.Linear(lstm_size, fc1_size)
        self.do2  = nn.Dropout(dropout_prob)
        self.fc2 = nn.Linear(fc1_size, n_classes)

    def forward(self, x_batch):
        # not sure where exactly Dropout is used in [14]
        _, last_lstm_out = self.lstm(x_batch)
        (h_last, c_last) = last_lstm_out
        # reshape to (batch_size, hidden_size)
        h_last = h_last [-1]
        do1_out = self.do1(h_last)
        fc1_out = F.relu(self.fc1(do1_out))
        do2_out = self.do2(fc1_out)
        # fc2_out = F.log_softmax(self.fc2(do2_out), dim=1)
        fc2_out = self.fc2(do2_out)
        return fc2_out

## this is the network used in the paper. It is a 1D conv with dilation
class Net_paper(nn.Module):
    
    def __init__(self, history_signal,num_inputs):
        super(Net_paper, self).__init__()

        self.b0_tcn0 = nn.Conv1d(num_inputs, 32, 3, dilation=2, padding=2)
        self.b0_tcn0_BN = nn.BatchNorm1d(32)
        self.b0_tcn0_ReLU = nn.ReLU()
        self.b0_tcn1 = nn.Conv1d(32, 64, 3, dilation=2, padding=2)
        self.b0_conv_pool = torch.nn.AvgPool1d(3, stride=2, padding=1)
        self.b0_tcn1_BN = nn.BatchNorm1d(64)
        self.b0_tcn1_ReLU = nn.ReLU()

        self.b1_tcn0 = nn.Conv1d(64, 64, 3, dilation=2, padding=2)
        self.b1_tcn0_BN = nn.BatchNorm1d(64)
        self.b1_tcn0_ReLU = nn.ReLU()
        self.b1_tcn1 = nn.Conv1d(64, 128, 3, dilation=2, padding=2)
        self.b1_conv_pool = torch.nn.AvgPool1d(3, stride=2, padding=1)
        self.b1_tcn1_BN = nn.BatchNorm1d(128)
        self.b1_tcn1_ReLU = nn.ReLU()

        self.b2_tcn0 = nn.Conv1d(128, 128, 3, dilation=4, padding=4)
        self.b2_tcn0_BN = nn.BatchNorm1d(128)
        self.b2_tcn0_ReLU = nn.ReLU()
        self.b2_tcn1 = nn.Conv1d(128, 128, 3, dilation=4, padding=4)
        self.b2_conv_pool = torch.nn.AvgPool1d(3, stride=2, padding=1)
        self.b2_tcn1_BN = nn.BatchNorm1d(128)
        self.b2_tcn1_ReLU = nn.ReLU()

        dim_fc = int(math.ceil(math.ceil(math.ceil(history_signal/2)/2)/2)*128)
        self.FC0 = nn.Linear(dim_fc, 256) # 592 in the Excel, 768 ours with pooling
        self.FC0_BN = nn.BatchNorm1d(256)
        self.FC0_ReLU = nn.ReLU()
        self.FC0_dropout = nn.Dropout(0.5)
        
        self.FC1 = nn.Linear(256, 64)
        self.FC1_BN = nn.BatchNorm1d(64)
        self.FC1_ReLU = nn.ReLU()
        self.FC1_dropout = nn.Dropout(0.5)
        
        self.GwayFC = nn.Linear(64, 2)
        

    def forward(self, x): # computation --> Pool --> BN --> activ --> dropout

        x = self.b0_tcn0_ReLU(self.b0_tcn0_BN(self.b0_tcn0(x)))
        x = self.b0_tcn1_ReLU(self.b0_tcn1_BN(self.b0_conv_pool(self.b0_tcn1(x))))

        x = self.b1_tcn0_ReLU(self.b1_tcn0_BN(self.b1_tcn0(x)))
        x = self.b1_tcn1_ReLU(self.b1_tcn1_BN(self.b1_conv_pool(self.b1_tcn1(x))))
        
        x = self.b2_tcn0_ReLU(self.b2_tcn0_BN(self.b2_tcn0(x)))
        x = self.b2_tcn1_ReLU(self.b2_tcn1_BN(self.b2_conv_pool(self.b2_tcn1(x))))
        
        x = x.flatten(1)

        x = self.FC0_dropout(self.FC0_ReLU(self.FC0_BN(self.FC0(x))))
        x = self.FC1_dropout(self.FC1_ReLU(self.FC1_BN(self.FC1(x))))
        x = self.GwayFC(x)
        
        return x

## called inside Classification
def init_net(lr, history_signal, num_inputs):
    net = Net_paper(history_signal,num_inputs)
    optimizer = getattr(optim, 'Adam')(net.parameters(), lr=lr)
    if torch.cuda.is_available():
        print('Moving model to cuda')
        net.cuda()
    else:
        print('Model to cpu')
    return net, optimizer

# reported metrics for test dataset
def report_metrics(Y_test_real, prediction, metric):
    Y_test_real = np.asarray(Y_test_real)
    prediction = np.asarray(prediction)
    prediction_1_true = prediction[Y_test_real==1]
    prediction_0_true = prediction[Y_test_real==0]
    total_1 = len(prediction_1_true)
    total_0 = len(prediction_0_true)
    predicted_correct_1 = sum(prediction_1_true)
    for m in metric:
        if m == 'RMSE':
            print('SCORE RMSE: %.3f' % np.sqrt(mean_squared_error(Y_test_real,prediction)))
        elif m == 'MAE':
            print('SCORE MAE: %.3f' % mean_absolute_error(Y_test_real,prediction))
        elif m == 'FDR':
            print('SCORE FDR: %.3f' % (sum(prediction_1_true)/total_1*100))
        elif m == 'FAR':
            print('SCORE FAR: %.3f' % (sum(prediction_0_true)/total_0*100))
        elif m == 'F1':
            print('SCORE F1: %.3f' % f1_score(Y_test_real,prediction))
        elif m == 'recall':
            print('SCORE recall: %.3f' % recall_score(Y_test_real,prediction))
        elif m == 'precision':
            print('SCORE precision: %.3f' % precision_score(Y_test_real,prediction))
    return f1_score(Y_test_real,prediction)

def train(ep,Xtrain,ytrain, batchsize, optimizer, model,Xtest,ytest):
    train_loss = 0
    Xtrain, ytrain = shuffle(Xtrain, ytrain)
    model.train()
    samples, features, dim_window = Xtrain.shape
    nbatches = Xtrain.shape[0]//batchsize
    correct=0
    # we weights the different classes. We both use an unbalance management and the weighting of the classes
    weights = [1.7, 0.3]
    # we use the GPU to train
    class_weights = torch.FloatTensor(weights).cuda()
    criterion = torch.nn.CrossEntropyLoss(weight=class_weights)
    predictions = np.ndarray(Xtrain.shape[0])
    #criterion = torch.nn.CrossEntropyLoss()
    for batch_idx in np.arange(nbatches+1):
        data = Xtrain[(batch_idx*batchsize):((batch_idx+1)*batchsize),:,:]
        target = ytrain[(batch_idx*batchsize):((batch_idx+1)*batchsize)]
        if torch.cuda.is_available():
            data, target = torch.Tensor(data).cuda(), torch.Tensor(target).cuda()
        else:
            data, target = torch.Tensor(data), torch.Tensor(target)
        data, target = Variable(data), Variable(target)
        optimizer.zero_grad()
        output = model(data)
        loss = criterion(output, target.long())
        loss.backward()
        optimizer.step()
        pred = output.data.max(1, keepdim=True)[1]
        predictions[(batch_idx*batchsize):((batch_idx+1)*batchsize)] = pred.cpu().numpy()[:,0]
        correct += pred.eq(target.data.view_as(pred)).cpu().sum()
        train_loss += loss
        if batch_idx > 0 and batch_idx % 10 == 0:
            print('Train Epoch: {} [{}/{} ({:.0f}%)] Loss: {:.6f} Accuracy: {} \r'.format(ep, batch_idx * batchsize, samples, (100. * batch_idx * batchsize) / samples, train_loss.item()/(10*batchsize), correct/((batch_idx+1) * batchsize)), end="\r")
            train_loss = 0
    print('T')
    F1 = report_metrics(ytrain, predictions, ['FDR','FAR','F1','recall', 'precision'])
    #at each epoch, we test the network to print the accuracy
    test(Xtest,ytest, model)
    return F1
    #test(Xtrain,ytrain, model)
    

def test(Xtest,ytest, model):
	# we evaluate the model at each epoch on the test dataset, the data that we do not use to train weights
    model.eval()
    test_loss = 0
    correct = 0
    batchsize = 30000
    nbatches = Xtest.shape[0]//batchsize
    predictions = np.ndarray(Xtest.shape[0])
    criterion = torch.nn.CrossEntropyLoss()
    # here we use a batch only because we can not load all the data in GPU memory
    with torch.no_grad():
        for batch_idx in np.arange(nbatches+1):
            data, target = Variable(torch.Tensor(Xtest[(batch_idx*batchsize):((batch_idx+1)*batchsize),:,:]), volatile=True), Variable(torch.Tensor(ytest[(batch_idx*batchsize):((batch_idx+1)*batchsize)]))
            if torch.cuda.is_available():
                data, target = data.cuda(), target.cuda()
            output = model(data)
            test_loss = criterion(output, target.long()).item()
            pred = output.data.max(1, keepdim=True)[1]
            correct += pred.eq(target.data.view_as(pred)).cpu().sum()
            predictions[(batch_idx*batchsize):((batch_idx+1)*batchsize)] = pred.cpu().numpy()[:,0]
    test_loss /= Xtest.shape[0]
    print('T')
    print('\nTest set: Average loss: {:.4f}, Accuracy: {}/{} ({:.0f}%)\n'.format(
                test_loss, correct, Xtest.shape[0],
                100. * correct / Xtest.shape[0]))
    report_metrics(ytest, predictions, ['FDR','FAR','F1','recall', 'precision'])
    return predictions

def net_train_validate(net,optimizer,Xtrain,ytrain,Xtest,ytest, epochs, batch_size, lr):
    ytest=ytest.values
    F1_list = np.ndarray(10)
    i=0
    for epoch in range(1, epochs):
    	# the train include also the test inside
        F1 = train(epoch,Xtrain,ytrain, batch_size, optimizer,net,Xtest,ytest)
        F1_list[i] = F1
        i+=1
        if i==5:
            i=0
        if F1_list[0]!=0 and (max(F1_list)-min(F1_list))==0:
            print("Exited beacause last 5 epochs has constant F1")
        if epoch % 20 == 0:
            lr /= 10
            for param_group in optimizer.param_groups:
                param_group['lr'] = lr
    print('T')


def train_LSTM(ep,train_loader, optimizer, model, Xtrain_examples):
    train_loss = 0
    model.train()
    correct=0
    weights = [1.9, 0.1]
    class_weights = torch.FloatTensor(weights).cuda()
    predictions = np.ndarray(Xtrain_examples)
    ytrain = np.ndarray(Xtrain_examples)
    #criterion = torch.nn.CrossEntropyLoss(weight=class_weights)
    criterion = torch.nn.CrossEntropyLoss()
    for i, data in enumerate(train_loader):
        sequences, labels = data
        batchsize = sequences.shape[1]
        sequences = sequences.cuda()
        labels = labels.cuda()
        optimizer.zero_grad()
        output = model(sequences)
        l = criterion(output, labels)
        l.backward()
        optimizer.step()
        pred = output.data.max(1, keepdim=True)[1]
        predictions[(i*batchsize):((i+1)*batchsize)] = pred.cpu().numpy()[:,0]
        ytrain[(i*batchsize):((i+1)*batchsize)] = labels.cpu().numpy()
        correct += pred.eq(labels.data.view_as(pred)).cpu().sum()
        train_loss += l.item()
        if i > 0 and i % 10 == 0:
            print('Train Epoch: {} [{}/{} ({:.0f}%)] Loss: {:.6f} Accuracy: {} \r'.format(ep, i * batchsize, Xtrain_examples, (100. * i * batchsize) / Xtrain_examples, train_loss/(10*batchsize), float(correct)/((i+1) * batchsize)), end="\r")
            train_loss = 0
    print('T')
    ytrain=ytrain[:((i+1)*batchsize)]
    predictions=predictions[:((i+1)*batchsize)]
    F1 = report_metrics(ytrain, predictions, ['FDR','FAR','F1','recall', 'precision'])
    return F1

def test_LSTM(model, test_loader, Xtest_examples):
    model.eval()
    test_loss = 0
    correct = 0
    criterion = torch.nn.CrossEntropyLoss()
    with torch.no_grad():
        test_preds = torch.as_tensor([])
        test_labels = torch.as_tensor([], dtype=torch.long)
        for i, test_data in enumerate(test_loader):
            sequences, labels = test_data
            sequences = sequences.cuda()
            labels = labels.cuda()
            preds = model(sequences)
            pred = preds.data.max(1, keepdim=True)[1]
            l = criterion(preds, labels)
            test_loss += l.item()
            correct += pred.eq(labels.data.view_as(pred)).cpu().sum()
            test_preds = torch.cat((test_preds, preds.cpu()))
            test_labels = torch.cat((test_labels, labels.cpu()))
            _, pred_labels = torch.max(test_preds, 1)
    print('\nTest set: Average loss: {:.4f}, Accuracy: {}/{} ({:.0f}%)\n'.format(
                test_loss, correct, Xtest_examples,
                100. * correct / Xtest_examples))
    report_metrics(test_labels, pred_labels, ['FDR','FAR','F1','recall', 'precision'])

def net_train_validate_LSTM(net,optimizer,train_loader,test_loader, epochs,Xtest_examples, Xtrain_examples, lr):
    # Training Loop
    F1_list = np.ndarray(5)
    i=0
    # identical to net_train_validate but train and test are separated and train does not include test
    for epoch in range(1,epochs):
        F1 = train_LSTM(epoch,train_loader, optimizer, net, Xtrain_examples)
        test_LSTM(net, test_loader, Xtest_examples)
        F1_list[i] = F1
        i+=1
        if i==5:
            i=0
        if F1_list[0]!=0 and (max(F1_list)-min(F1_list))==0:
            print("Exited beacause last 5 epochs has constant F1")
        if epoch % 20 == 0:
            lr /= 10
            for param_group in optimizer.param_groups:
                param_group['lr'] = lr
    print('T')