import os
from typing import List

from frcnn.voc import PascalVocData
from tests import DATASET_ROOT_PATH


def test_voc_dataset_integrity():
    """
     - check if an image file exists
     - identify duplicate nms in annotation data
    :return:
    """
    voc = PascalVocData(DATASET_ROOT_PATH)
    train, test, classes = voc.load_data()

    # check function
    def check(data: List[dict], visualize=True):
        if not hasattr(check, '_duplicate'):
            check._duplicate = list()

        for x in data:
            # Check whether an image file exists
            assert os.path.exists(x['image_path'])

            # Check duplicate
            assert x['image_path'] not in check._duplicate
            check._duplicate.append(x['image_path'])

            # if visualize:
            #     voc.visualize_img(x, 'files/haha{0}.jpg'.format(len(check._duplicate)))

    check(train)
    check(test)


def test_voc_dataset_count():
    voc = PascalVocData('/data/VOCdevkit')
    train, test, classes = voc.load_data()

    print(len(train))

    # voc.count_class(train)
    # voc.count_class(test)
