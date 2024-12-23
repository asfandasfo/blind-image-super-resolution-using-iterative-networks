import logging
from collections import OrderedDict
import torch
import torch.nn as nn
import torch.nn.init as init
from torch.nn.parallel import DataParallel
from models.networks import Predictor
from .base_model import BaseModel


logger = logging.getLogger('base')


class P_Model(BaseModel):
    def __init__(self, opt):
        super(P_Model, self).__init__(opt)

        if opt['dist']:
            self.rank = torch.distributed.get_rank()
        else:
            self.rank = -1  # non dist training
        train_opt = opt['train']
        opt_net = opt['network_G']
        # define network and load pretrained models
        self.netG = Predictor(in_nc=opt_net['in_nc'], nf=opt_net['nf'], code_len=opt_net['code_length']).to(self.device)
        self.netG = DataParallel(self.netG)
        # print network
        self.print_network()
        
        self.load()

        if self.is_train:
            #self.init_model()
            self.netG.train()

            # loss
            loss_type = train_opt['pixel_criterion']
            if loss_type == 'l1':
                self.cri_pix = nn.L1Loss().to(self.device)
            elif loss_type == 'l2':
                self.cri_pix = nn.MSELoss().to(self.device)
            else:
                raise NotImplementedError('Loss type [{:s}] is not recognized.'.format(loss_type))
            self.l_pix_w = train_opt['pixel_weight']

            # optimizers
            wd_G = train_opt['weight_decay_G'] if train_opt['weight_decay_G'] else 0
            optim_params = []
            for k, v in self.netG.named_parameters():  # can optimize for a part of the model
                if v.requires_grad:
                    optim_params.append(v)
                else:
                    if self.rank <= 0:
                        logger.warning('Params [{:s}] will not optimize.'.format(k))
            self.optimizer_G = torch.optim.Adam(optim_params, lr=train_opt['lr_G'],
                                                weight_decay=wd_G,
                                                betas=(train_opt['beta1'], train_opt['beta2']))
            #self.optimizer_G = torch.optim.SGD(optim_params, lr=train_opt['lr_G'], momentum=0.9)
            self.optimizers.append(self.optimizer_G)
            self.log_dict = OrderedDict()

    def init_model(self, scale=0.1):
        # Common practise for initialization.
        for layer in self.netG.modules():
            if isinstance(layer, nn.Conv2d):
                init.kaiming_normal_(layer.weight, a=0, mode='fan_in')
                layer.weight.data *= scale  # for residual block
                if layer.bias is not None:
                    layer.bias.data.zero_()
            elif isinstance(layer, nn.Linear):
                init.kaiming_normal_(layer.weight, a=0, mode='fan_in')
                layer.weight.data *= scale
                if layer.bias is not None:
                    layer.bias.data.zero_()
            elif isinstance(layer, nn.BatchNorm2d):
                init.constant_(layer.weight, 1)
                init.constant_(layer.bias.data, 0.0)


    def feed_data(self, lr_img, ker_map=None):
        self.var_L = lr_img.to(self.device)  # LQ
        if ker_map is not None:
            self.real_ker = ker_map.to(self.device)  # real kernel map

    def optimize_parameters(self, step):
        self.optimizer_G.zero_grad()
        self.fake_ker = self.netG(self.var_L)
        l_pix = self.l_pix_w * self.cri_pix(self.fake_ker, self.real_ker)
        l_pix.backward()
        self.optimizer_G.step()
        # set log
        self.log_dict['l_pix'] = l_pix.item()

    def test(self):
        self.netG.eval()
        with torch.no_grad():
            self.fake_ker = self.netG(self.var_L)
        self.netG.train()


    def get_current_log(self):
        return self.log_dict

    def get_current_visuals(self):
        out_dict = OrderedDict()
        out_dict['est_ker_map'] = self.fake_ker.detach()[0].float().cpu() # for validation
        out_dict['LQ'] = self.var_L.detach()[0].float().cpu()
        out_dict['Batch_est_ker_map'] = self.fake_ker.detach().float().cpu() # Batch est_ker_map, for train
        out_dict['Batch_LQ'] = self.var_L.detach().float().cpu()
        #out_dict['SR'] = self.fake_H.detach()[0].float().cpu()
        #out_dict['GT'] = self.real_H.detach()[0].float().cpu()
        return out_dict

    def print_network(self):
        s, n = self.get_network_description(self.netG)
        if isinstance(self.netG, nn.DataParallel):
            net_struc_str = '{} - {}'.format(self.netG.__class__.__name__,
                                             self.netG.module.__class__.__name__)
        else:
            net_struc_str = '{}'.format(self.netG.__class__.__name__)
        if self.rank <= 0:
            logger.info('Network G structure: {}, with parameters: {:,d}'.format(net_struc_str, n))
            logger.info(s)

    def load(self):
        load_path_G = self.opt['path']['pretrain_model_G']
        if load_path_G is not None:
            logger.info('Loading model for G [{:s}] ...'.format(load_path_G))
            self.load_network(load_path_G, self.netG, self.opt['path']['strict_load'])

    def save(self, iter_label):
        self.save_network(self.netG, 'G', iter_label)
