#! -*- coding:utf-8 -*-
# 情感分析例子，利用MLM+P-tuning

import torch
import torch.nn as nn
import numpy as np
from bert4torch.tokenizers import Tokenizer
from bert4torch.models import build_transformer_model
from torch.optim import Adam
from bert4torch.snippets import sequence_padding, ListDataset, Callback
from torch.utils.data import DataLoader


maxlen = 128
batch_size = 32
config_path = 'F:/Projects/pretrain_ckpt/robert/[hit_torch_base]--chinese-roberta-wwm-ext-base/config.json'
checkpoint_path = 'F:/Projects/pretrain_ckpt/robert/[hit_torch_base]--chinese-roberta-wwm-ext-base/pytorch_model.bin'
dict_path = 'F:/Projects/pretrain_ckpt/robert/[hit_torch_base]--chinese-roberta-wwm-ext-base/vocab.txt'
device = 'cuda' if torch.cuda.is_available() else 'cpu'


def load_data(filename):
    D = []
    with open(filename, encoding='utf-8') as f:
        for l in f:
            text, label = l.strip().split('\t')
            D.append((text, int(label)))
    return D

# 加载数据集
train_data = load_data('E:/Github/bert4torch/examples/datasets/sentiment/sentiment.train.data')
valid_data = load_data('E:/Github/bert4torch/examples/datasets/sentiment/sentiment.valid.data')
test_data = load_data('E:/Github/bert4torch/examples/datasets/sentiment/sentiment.test.data')

# 模拟标注和非标注数据
train_frac = 0.01  # 标注数据的比例
num_labeled = int(len(train_data) * train_frac)
unlabeled_data = [(t, 2) for t, l in train_data[num_labeled:]]
train_data = train_data[:num_labeled]
# train_data = train_data + unlabeled_data

# 建立分词器
tokenizer = Tokenizer(dict_path, do_lower_case=True)

# 对应的任务描述
mask_idx = 5
desc = ['[unused%s]' % i for i in range(1, 9)]
desc.insert(mask_idx - 1, '[MASK]')
desc_ids = [tokenizer.token_to_id(t) for t in desc]
pos_id = tokenizer.token_to_id(u'很')
neg_id = tokenizer.token_to_id(u'不')


def random_masking(token_ids):
    """对输入进行随机mask
    """
    rands = np.random.random(len(token_ids))
    source, target = [], []
    for r, t in zip(rands, token_ids):
        if r < 0.15 * 0.8:
            source.append(tokenizer._token_mask_id)
            target.append(t)
        elif r < 0.15 * 0.9:
            source.append(t)
            target.append(t)
        elif r < 0.15:
            source.append(np.random.choice(tokenizer._vocab_size - 1) + 1)
            target.append(t)
        else:
            source.append(t)
            target.append(0)
    return source, target


random = True
def collate_fn(batch):
    batch_token_ids, batch_segment_ids, batch_output_ids = [], [], []
    for text, label in batch:
        token_ids, segment_ids = tokenizer.encode(text, maxlen=maxlen)
        if label != 2:
            token_ids = token_ids[:1] + desc_ids + token_ids[1:]
            segment_ids = [0] * len(desc_ids) + segment_ids
        if random:
            source_ids, target_ids = random_masking(token_ids)
        else:
            source_ids, target_ids = token_ids[:], token_ids[:]
        if label == 0:
            source_ids[mask_idx] = tokenizer._token_mask_id
            target_ids[mask_idx] = neg_id
        elif label == 1:
            source_ids[mask_idx] = tokenizer._token_mask_id
            target_ids[mask_idx] = pos_id
        batch_token_ids.append(source_ids)
        batch_segment_ids.append(segment_ids)
        batch_output_ids.append(target_ids)
    batch_token_ids = torch.tensor(sequence_padding(batch_token_ids), dtype=torch.long, device=device)
    batch_segment_ids = torch.tensor(sequence_padding(batch_segment_ids), dtype=torch.long, device=device)
    batch_output_ids = torch.tensor(sequence_padding(batch_output_ids), dtype=torch.long, device=device)
    return [batch_token_ids, batch_segment_ids], batch_output_ids

# 加载数据集
train_dataloader = DataLoader(ListDataset(data=train_data), batch_size=batch_size, shuffle=True, collate_fn=collate_fn)
random = False
valid_dataloader = DataLoader(ListDataset(data=valid_data), batch_size=batch_size, collate_fn=collate_fn) 
test_dataloader = DataLoader(ListDataset(data=test_data),  batch_size=batch_size, collate_fn=collate_fn) 

class MyLoss(nn.CrossEntropyLoss):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
    def forward(self, y_preds, y_true):
        # accuracy = keras.metrics.sparse_categorical_accuracy(y_true, y_pred)
        # accuracy = K.sum(accuracy * y_mask) / K.sum(y_mask)
        # self.add_metric(accuracy, name='accuracy')
        y_pred = y_preds[1]
        y_pred = y_pred.reshape(-1, y_pred.shape[-1])
        loss = super().forward(y_pred, y_true.flatten())
        return loss

# 加载预训练模型
model = build_transformer_model(config_path=config_path, checkpoint_path=checkpoint_path, with_mlm=True).to(device)

12# 定义使用的loss和optimizer，这里支持自定义
model.compile(
    loss=MyLoss(ignore_index=0),
    optimizer=Adam(model.parameters(), lr=2e-5),  # 用足够小的学习率
)

class PtuningEmbedding(Embedding):
    """新定义Embedding层，只优化部分Token
    """
    def call(self, inputs, mode='embedding'):
        embeddings = self.embeddings
        embeddings_sg = K.stop_gradient(embeddings)
        mask = np.zeros((K.int_shape(embeddings)[0], 1))
        mask[1:9] += 1  # 只优化id为1～8的token
        self.embeddings = embeddings * mask + embeddings_sg * (1 - mask)
        outputs = super(PtuningEmbedding, self).call(inputs, mode)
        self.embeddings = embeddings
        return outputs


class PtuningBERT(BERT):
    """替换原来的Embedding
    """
    def apply(self, inputs=None, layer=None, arguments=None, **kwargs):
        if layer is Embedding:
            layer = PtuningEmbedding
        return super(PtuningBERT, self).apply(inputs, layer, arguments, **kwargs)


# 加载预训练模型
model = build_transformer_model(
    config_path=config_path,
    checkpoint_path=checkpoint_path,
    model=PtuningBERT,
    with_mlm=True
)

for layer in model.layers:
    if layer.name != 'Embedding-Token':
        layer.trainable = False

# 训练用模型
y_in = keras.layers.Input(shape=(None,))
output = keras.layers.Lambda(lambda x: x[:, :10])(model.output)
outputs = CrossEntropy(1)([y_in, model.output])

train_model = keras.models.Model(model.inputs + [y_in], outputs)
train_model.compile(optimizer=Adam(6e-4))
train_model.summary()

# 预测模型
model = keras.models.Model(model.inputs, output)

# 转换数据集
train_generator = data_generator(train_data, batch_size)
valid_generator = data_generator(valid_data, batch_size)
test_generator = data_generator(test_data, batch_size)


class Evaluator(Callback):
    """评估与保存
    """
    def __init__(self):
        self.best_val_acc = 0.

    def on_epoch_end(self, global_step, epoch, logs=None):
        val_acc = self.evaluate(valid_dataloader)
        test_acc = self.evaluate(train_dataloader)
        if val_acc > self.best_val_acc:
            self.best_val_acc = val_acc
            # model.save_weights('best_model.pt')
        print(f'[{choice}]  valid_acc: {val_acc:.4f}, test_acc: {test_acc:.4f}, best_val_acc: {self.best_val_acc:.4f}\n')

    @staticmethod
    def evaluate(data):
        total, right = 0., 0.
        for x_true, y_true in data:
            y_pred = model.predict(x_true)[1]
            y_pred = y_pred[:, mask_idx, [neg_id, pos_id]].argmax(axis=1)
            y_true = (y_true[:, mask_idx] == pos_id).long()
            total += len(y_true)
            right += (y_true == y_pred).sum().item()
        return right / total


if __name__ == '__main__':

    evaluator = Evaluator()

    train_model.fit_generator(
        train_generator.forfit(),
        steps_per_epoch=len(train_generator) * 50,
        epochs=1000,
        callbacks=[evaluator]
    )

else:

    model.load_weights('best_model_bert.weights')