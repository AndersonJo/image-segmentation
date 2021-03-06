import os
from argparse import ArgumentParser

from frcnn.logging import get_logger
from frcnn.tools import denormalize_image

__version__ = 0.2

logger = get_logger(__name__)

# Parser:: Basic Arguments
parser = ArgumentParser(description='Faster R-CNN')
parser.add_argument('--mode', default='train', type=str, help='train or test')
parser.add_argument('--data', default='/data/VOCdevkit', type=str, help='the path of VOC or COCO dataset')
parser.add_argument('--gpu', default='0', type=str, help='Specify which gpu to use as a number')

# Parser:: Base Model (Feature Extraction Network)
parser.add_argument('--net', default='vgg19', type=str, help='base network (vgg, resnet)')

# Parser:: Reginon Proposal Network & Anchor
parser.add_argument('--rescale', default=True, type=bool, help='Rescale input image to lager one')
parser = parser.parse_args()

os.environ["CUDA_VISIBLE_DEVICES"] = parser.gpu

import cv2
import keras.backend as K
import numpy as np
import tensorflow as tf
from keras.utils import Progbar

from frcnn.debug import visualize_gta, RPNTrainerDebug, FRCNNDebug
from frcnn.classifier_trainer import ClassifierTrainer
from frcnn.config import singleton_config, Config
from frcnn.frcnn import FRCNN
from frcnn.nms import non_max_suppression
from frcnn.rpn_trainer import RPNTrainer, RPNDataProcessor
from frcnn.voc import PascalVocData

# Momory Limit & Debugging
tf_config = tf.ConfigProto()
tf_config.gpu_options.per_process_gpu_memory_fraction = 1.
tf_config.gpu_options.allow_growth = True

sess = tf.Session(config=tf_config)
# sess = tf_debug.LocalCLIDebugWrapperSession(sess)
K.set_session(sess)


def train_voc(config: Config, train: list, class_mapping: dict):
    class_mapping_inv = {v: k for k, v in class_mapping.items()}
    n_cls = len(class_mapping)

    # Parameters
    best_loss = np.inf
    global_step = 0

    # Load training tools
    rpn_trainer = RPNTrainer(train, shuffle=True, augment=True)
    clf_trainer = ClassifierTrainer(config, class_mapping)

    # Create Model
    frcnn = FRCNN(config, class_mapping, train=True)
    # global_step, best_loss, checkpoint_filename = frcnn.load_latest_model()

    for epoch in range(100):
        # Progress Bar
        progbar = Progbar(len(train), width=10, stateful_metrics=['iou', 'gta'])
        for step in range(len(train)):
            # Get VOC data and RPN targets
            batch_image, original_image, batch_cls, batch_regr, meta = rpn_trainer.next_batch(debug=False)
            # RPNTrainerDebug.debug_next_batch(batch_image[0].copy(), meta, batch_cls, batch_regr)  # DEBUG

            # Train Region Proposal Network
            rpn_loss = frcnn.rpn_model.train_on_batch(batch_image, [batch_cls, batch_regr])

            # Train Classifier Network
            if global_step % 2 == 0:
                rpn_cls, rpn_reg = frcnn.rpn_model.predict_on_batch(batch_image)
            else:
                rpn_cls = batch_cls[:, :, :, frcnn.rpn.n_anchor:]
                rpn_reg = batch_regr[:, :, :, frcnn.rpn.n_anchor * 4:]

            # anchors: (min_x, min_y, max_y, max_y)
            anchors, probs = frcnn.generate_anchors(rpn_cls, rpn_reg)
            anchors, probs = non_max_suppression(anchors, probs, overlap_threshold=0.7, max_box=300)
            # FRCNNDebug.debug_generate_anchors(batch_image[0].copy(), meta, anchors, probs)

            # rois: (min_x, min_y, w, h)
            rois, cls_y, reg_y, best_ious = clf_trainer.next_batch(anchors, meta,
                                                                   image=batch_image[0].copy(),
                                                                   debug_image=False)

            if rois is None:
                continue

            # DEBUG
            # if cls_y is not None:
            #     ClassifierDebug.debug_next_batch(batch_image[0].copy(), meta, rois, cls_y, reg_y,
            #                                      class_mapping, class_mapping_inv)

            clf_loss = frcnn.clf_model.train_on_batch([batch_image, rois], [cls_y, reg_y])

            # DEBUG cls_y, reg_y, rois
            # cls_pred, anc_pred = frcnn.to_anchors(cls_y, reg_y[:, :, (n_cls - 1) * 4:], rois,
            #                                       clf_threshold=0.7)
            # anc_pred, cls_pred = non_max_suppression(anc_pred, cls_pred, overlap_threshold=0.8)
            # visualize(batch_image[0].copy(), meta, cls_pred, anc_pred, class_mapping, class_mapping_inv)

            # Save the Model
            total_loss = rpn_loss[0] + clf_loss[0]
            if (total_loss < best_loss and global_step > 1000) or (global_step % 1000 == 0 and global_step > 1000):
                if total_loss < best_loss:
                    best_loss = total_loss

                global_step = int(global_step)
                filename = 'checkpoints/model_{0}_{1:.4}.hdf5'.format(global_step, round(total_loss, 4))
                frcnn.save(filename)

            # Progress Bar
            n_gta = len(meta['objects'])
            n_best_ious = len(best_ious[best_ious > 0.7])

            global_step += 1
            progbar.update(step, [('rpn_c', rpn_loss[1]),
                                  ('rpn_r', rpn_loss[2]),
                                  ('clf_c', clf_loss[1]),
                                  ('clf_r', clf_loss[2]),
                                  ('iou', n_best_ious),
                                  ('gta', n_gta)
                                  ])
            print()


def test_voc(config: Config, test: list, class_mapping: dict):
    class_mapping_inv = {v: k for k, v in class_mapping.items()}

    # Load data tools
    rpn_data = RPNDataProcessor(test, shuffle=False, augment=False)

    # Create Model
    frcnn = FRCNN(config, class_mapping, train=False)
    frcnn.load_latest_model()

    # Inference
    for step in range(rpn_data.count()):
        batch_image, original_image, meta = rpn_data.next_batch()

        rpn_cls, rpn_reg, f_maps = frcnn.rpn_model.predict_on_batch(batch_image)
        # DEBUG
        FRCNNDebug.debug_predict(batch_image[0].copy(), meta, rpn_cls, rpn_reg)

        anchors, probs = frcnn.generate_anchors(rpn_cls, rpn_reg)
        anchors, probs = non_max_suppression(anchors, probs, overlap_threshold=0.7, max_box=300)

        # DEBUG
        # FRCNNDebug.debug_generate_anchors(batch_image[0].copy(), meta, anchors, probs)

        # check_rois(f_maps, anchors)
        cls_pred, anc_pred = frcnn.clf_predict(f_maps, anchors, meta=meta)
        anc_pred, cls_pred = non_max_suppression(anc_pred, cls_pred, overlap_threshold=0.5)
        if anc_pred is not None:
            pass
            # visualize(batch_image[0].copy(), meta, cls_pred, anc_pred, class_mapping, class_mapping_inv)

        # if gta_regs is not None:


def visualize(image, meta, cls_p, anc_p, class_mapping, class_mapping_inv):
    image = denormalize_image(image)
    visualize_gta(image, meta)

    # Test Classification
    bg_idx = class_mapping['bg']

    cls_pred = cls_p[np.where(cls_p != bg_idx)]
    cls_pred = [class_mapping_inv[cls_idx] for cls_idx in cls_pred]
    cls_true = [obj[0] for obj in meta['objects']]

    print('cls_pred:', cls_pred)
    print('cls_true:', cls_true)
    print()

    # Test Regression
    for anc in anc_p:
        min_x = int(anc[0])
        min_y = int(anc[1])
        max_x = int(anc[2])
        max_y = int(anc[3])
        cv2.rectangle(image, (min_x, min_y), (max_x, max_y), (255, 255, 0))

    cv2.imwrite('temp/{0}'.format(meta['filename']), image)


def main(config: Config):
    # Load data
    only = ['person', 'chair']

    logger.info('loading data')
    vocdata = PascalVocData(config.data_path, only=only)
    train, test, class_mapping = vocdata.load_data(add_bg=True)

    print(class_mapping)
    print('train:', len(train))
    print('test:', len(test))
    print('only:', only)

    # Set Random Seed
    # np.random.seed(0)
    # tf.set_random_seed(0)

    if parser.mode == 'train':
        train_voc(config, train, class_mapping)
    elif parser.mode == 'test':
        test_voc(config, test, class_mapping)


if __name__ == '__main__':
    config = singleton_config(parser)
    main(config)
