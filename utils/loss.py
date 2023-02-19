import torch
from box import xywh2xyxy


class Loss:
    def __int__(self, device):
        self.device = device

    def build_targets(self, targets, batch_size, scale):
        if targets.shape[0] == 0:
            out = torch.zeros(batch_size, 0, 5, device=self.device)
        else:
            indices = targets[:, 0]
            index, count = indices.unique(return_counts=True)
            out = torch.zeros(batch_size, count.max(), 5, device=self.device)
            for index in range(batch_size):
                matches = indices == index
                num = matches.sum()
                if num:
                    out[index, :num] = targets[matches, 1:]

            out[:, :, 1:5] = xywh2xyxy(out[:, :, 1:5] * scale)

        return out

    def __call__(self, preds, targets):
        pass


def build_targets(targets, batch_size, scale):
    if targets.shape[0] == 0:
        out = torch.zeros(batch_size, 0, 5)
    else:
        indices = targets[:, 0]
        index, count = indices.unique(return_counts=True)
        out = torch.zeros(batch_size, count.max(), 5)
        for index in range(batch_size):
            matches = indices == index
            num = matches.sum()
            if num:
                out[index, :num] = targets[matches, 1:]

        out[:, :, 1:5] = xywh2xyxy(out[:, :, 1:5] * scale)

    return out


targets = torch.tensor(
    [[0.0000, 4.0000, 0.05, 0.05, 0.05, 0.05],
     [0.0000, 4.0000, 0.0986, 0.0311, 0.0279, 0.0553],
     [1.0000, 1.0000, 0.5304, 0.6816, 0.0253, 0.0137],
     [1.0000, 1.0000, 0.5733, 0.6805, 0.0169, 0.0137],
     [1.0000, 1.0000, 0.6636, 0.6742, 0.0246, 0.0222]]
)

batch_size = 2
scale = torch.tensor([20, 10, 20, 10])
targets = build_targets(targets, batch_size, scale)
gt_labels, gt_bboxes = targets.split((1, 4), 2)  # cls, xyxy
mask_gt = gt_bboxes.sum(2, keepdim=True).gt_(0)
print(targets)
print(gt_labels)
print(mask_gt)