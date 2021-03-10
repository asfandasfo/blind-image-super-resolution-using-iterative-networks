import logging
logger = logging.getLogger('base')


def create_model(opt):
    model = opt['model']

    if model == 'predictor':
        from .P_model import P_Model as M
    elif model == 'corrector':
        from .C_model import C_Model as M
    else:
        raise NotImplementedError('Model [{:s}] not recognized.'.format(model))
    m = M(opt)
    logger.info('Model [{:s}] is created.'.format(m.__class__.__name__))
    return m
