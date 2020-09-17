import os
from typing import Callable, Sequence, Union

import torch
from scipy import linalg
from torchvision import transforms

from ignite.exceptions import NotComputableError
from ignite.metrics.metric import Metric, reinit__is_reduced, sync_all_reduce


__all__ = ["FID"]

class DefaultDataset(data.Dataset):
    def __init__(self, root, transform=None):
        self.samples = listdir(root)
        self.samples.sort()
        self.transform = transform
        self.targets = None

    def __getitem__(self, index):
        fname = self.samples[index]
        img = Image.open(fname).convert('RGB')
        if self.transform is not None:
            img = self.transform(img)
        return img

    def __len__(self):
        return len(self.samples)

class FID(Metric):
    """Calculates FID metric

    """

    def __init__(
        self, 
        output_transform: Callable = lambda x: x, 
        input_path: os.path, 
        output_path: os.path,
        test_model = None,
    ):
        self._input_path = input_path
        self._output_path = output_path

        if test_model is None:
            try:
                from torchvision import models
                test_model = models.inception_v3()
            except ImportError:
                raise ValueError("Argument test_model should be set")
        
        self._test_model = test_model
    

    def _frechet_distance(mu, cov, mu2, cov2):
        cc, _ = linalg.sqrtm(torch.dot(cov, cov2), disp = False)
        dist = torch.sum((mu -mu2) ** 2) + torch.trace(cov + cov2 - 2 * cc)
        return torch.real(dist)
    
    def _get_eval_loader(root, img_size=256, batch_size=32,
                        imagenet_normalize=True, shuffle=True,
                        num_workers=4, drop_last=False):
        if imagenet_normalize:
            height, width = 299, 299
            mean = [0.485, 0.456, 0.406]
            std = [0.229, 0.224, 0.225]
        else:
            height, width = img_size, img_size
            mean = [0.5, 0.5, 0.5]
            std = [0.5, 0.5, 0.5]

        transform = transforms.Compose([
            transforms.Resize([img_size, img_size]),
            transforms.Resize([height, width]),
            transforms.ToTensor(),
            transforms.Normalize(mean = mean, std = std)
        ])

        dataset = DefaultDataset(root, transform = transform)
        return torch.utils.data.DataLoader(dataset = dataset,
                            batch_size = batch_size,
                            shuffle = shuffle,
                            num_workers = num_workers,
                            pin_memory = True,
                            drop_last = drop_last)
    
    def _cov(x, rowvar=False):
        # PyTorch implementation of numpy.cov from https://github.com/pytorch/pytorch/issues/19037
        if x.dim() == 1:
            x = x.view(-1, 1)

        avg = torch.mean(x, 0)
        fact = x.shape[0] - 1
        xm = x.sub(avg.expand_as(x))
        X_T = xm.t()
        c = torch.mm(X_T, xm)
        c = c / fact

        return c.squeeze()
    
    def _calculate_fid_given_paths(paths, img_size = 256, batch_size = 50):
        # calculating FID given two paths
        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        inception = self._test_model().eval().to(device)
        loaders = [self._get_eval_loader(path, img_size, batch_size) for path in paths]

        mu, cov = [], []
        for loader in loaders:
            actvs = []
            for x in range(len(loader)):
                actv = inception(x.to(device))
                actvs.append(actv)
            actvs = torch.cat(actvs, dim = 0).cpu().detach().numpy()
            mu.append(torch.mean(actvs, axis = 0))
            cov.append(_cov(actvs, rowvar = False))
        fid_value = self._frechet_distance(mu[0], cov[0], mu[1], cov[1])
        return fid_value

    @reinit__is_reduced
    def reset(self) -> None:
        self._value = None

    @reinit__is_reduced
    def update(self, output) -> None:
        pass

    @sync_all_reduce
    def compute(self) -> Union(torch.Tensor, float):
        self._value = _calculate_fid_given_paths([self._input_path, self._output_path])
        return self._value
