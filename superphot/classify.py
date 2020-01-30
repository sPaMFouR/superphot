#!/usr/bin/env python

import numpy as np
import matplotlib.pyplot as plt
import logging
from astropy.table import Table
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import KFold
from sklearn.metrics import confusion_matrix
from sklearn.preprocessing import scale
from sklearn.utils import safe_indexing, check_random_state
from imblearn.over_sampling.base import BaseOverSampler
from imblearn.utils import Substitution, _docstring
from imblearn.over_sampling import SMOTE
from .util import get_VAV19
import itertools
from tqdm import tqdm

logging.basicConfig(format='%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S', level=logging.INFO)
t_conf = Table.read(get_VAV19('ps1confirmed_only_sne.txt'), format='ascii')
classes = sorted(set(t_conf['type']))


def plot_confusion_matrix(cm, normalize=False, title='Confusion Matrix', cmap='Blues'):
    """
    This function prints and plots the confusion matrix.
    Normalization can be applied by setting `normalize=True`.
    From tutorial: https://scikit-learn.org/stable/auto_examples/model_selection/plot_confusion_matrix.html
    """
    if normalize:
        cm = cm.astype('float') / cm.sum(axis=1)[:, np.newaxis]
    plt.figure(figsize=(6., 6.))
    plt.imshow(cm, interpolation='nearest', cmap=cmap, aspect='equal')
    plt.title(title)
    tick_marks = np.arange(len(classes))
    plt.xticks(tick_marks, classes, rotation=45)
    plt.yticks(tick_marks, classes)
    plt.ylim(4.5, -0.5)

    fmt = '.2f' if normalize else 'd'
    thresh = cm.max() / 2.
    for i, j in itertools.product(range(cm.shape[0]), range(cm.shape[1])):
        plt.text(j, i, format(cm[i, j], fmt),
                 horizontalalignment="center",
                 color="white" if cm[i, j] > thresh else "black")

    plt.ylabel('True Label')
    plt.xlabel('Predicted Label')
    plt.tight_layout()
    plt.savefig('confusion_matrix_norm.pdf' if normalize else 'confusion_matrix.pdf')


@Substitution(
    sampling_strategy=BaseOverSampler._sampling_strategy_docstring.replace('dict or callable', 'dict, callable or int'),
    random_state=_docstring._random_state_docstring)
class MultivariateGaussian(BaseOverSampler):
    """Class to perform over-sampling using a multivariate Gaussian (``numpy.random.multivariate_normal``).

    Parameters
    ----------
    {sampling_strategy}

        - When ```int``, it corresponds to the total number of samples in each
          class (including the real samples). Can be used to oversample even
          the majority class.

    {random_state}
    """
    def __init__(self, sampling_strategy='all', random_state=None):
        self.random_state = random_state
        if isinstance(sampling_strategy, int):
            self.samples_per_class = sampling_strategy
            sampling_strategy = 'all'
        else:
            self.samples_per_class = None
        super().__init__(sampling_strategy=sampling_strategy)

    def _fit_resample(self, X, y):
        self.fit(X, y)

        X_resampled = X.copy()
        y_resampled = y.copy()

        for class_sample, n_samples in self.sampling_strategy_.items():
            target_class_indices = np.flatnonzero(y == class_sample)
            if self.samples_per_class is not None:
                n_samples = self.samples_per_class - len(target_class_indices)
            if n_samples == 0:
                continue
            X_class = safe_indexing(X, target_class_indices)

            mean = np.mean(X_class, axis=0)
            cov = np.cov(X_class, rowvar=False)
            rs = check_random_state(self.random_state)
            X_new = rs.multivariate_normal(mean, cov, n_samples)
            y_new = np.repeat(class_sample, n_samples)

            X_resampled = np.vstack((X_resampled, X_new))
            y_resampled = np.hstack((y_resampled, y_new))

        return X_resampled, y_resampled


def train_classifier(data, n_est=100, depth=None, max_feat=None, n_jobs=-1, sampler_type='mvg'):
    """
    Initialize and train a random forest classifier. Balance the classes before training by oversampling.

    Parameters
    ----------
    data : astropy.table.Table
        Astropy table containing the training data. Must have a 'features' column and a 'label' (integers) column.
    n_est: int, optional
        The number of trees in the forest. Default: 100.
    depth : int, optional
        The maxiumum depth of a tree. If None, the tree will have all pure leaves.
    max_feat : int, optional
        The maximum number of used before making a split. If None, use all features.
    n_jobs : int, optional
        The number of jobs to run in parallel for the classifier. If -1, use all available processors.
    sampler_type : str, optional
        The type of resampler to use. Current choices are 'mvg' (multivariate Gaussian; default) or 'smote' (synthetic
        minority oversampling technique).

    Returns
    -------
    clf : sklearn.emsemble.RandomForestClassifier
        A random forest classifier trained from the classified transients.
    sampler : imblearn.over_sampling.base.BaseOverSampler
        A resampler used to balance the training sample.
    """
    clf = RandomForestClassifier(n_estimators=n_est, max_depth=depth, class_weight='balanced',
                                 criterion='entropy', max_features=max_feat, n_jobs=n_jobs)
    if sampler_type == 'mvg':
        sampler = MultivariateGaussian(sampling_strategy=1000)
    elif sampler_type == 'smote':
        sampler = SMOTE()
    else:
        raise NotImplementedError(f'{sampler_type} is not a recognized sampler type')
    features_resamp, labels_resamp = sampler.fit_resample(data['features'], data['label'])
    clf.fit(features_resamp, labels_resamp)
    return clf, sampler


def validate_classifier(clf, sampler, data):
    """
    Validate the performance of a machine-learning classifier using leave-one-out cross-validation. The results are
    plotted as a confusion matrix, which is saved as a PDF.

    Parameters
    ----------
    clf : sklearn.emsemble.RandomForestClassifier
        The classifier to validate.
    sampler : imblearn.over_sampling.SMOTE
        First resample the data using this sampler.
    data : astropy.table.Table
        Astropy table containing the training data. Must have a 'features' column and a 'label' (integers) column.
    """
    kf = KFold(len(np.unique(data['id'])))
    labels_test = np.empty_like(data['label'])
    pbar = tqdm(desc='Cross-validation', total=kf.n_splits)
    for train_index, test_index in kf.split(data):
        features_resamp, labels_resamp = sampler.fit_resample(data['features'][train_index], data['label'][train_index])
        clf.fit(features_resamp, labels_resamp)
        labels_test[test_index] = clf.predict(data['features'][test_index])
        pbar.update()
    pbar.close()

    cnf_matrix = confusion_matrix(data['label'], labels_test)
    plot_confusion_matrix(cnf_matrix)
    plot_confusion_matrix(cnf_matrix, normalize=True)
    return cnf_matrix


def load_test_data():
    test_table = Table.read('test_data.txt', format='ascii.fixed_width', fill_values=('', ''))
    test_table['features'] = np.load('test_data.npz')['features']
    logging.info('test data loaded from test_data.txt and test_data.npz')
    return test_table


def main():
    logging.info('started classify.py')
    test_data = load_test_data()
    test_data['features'] = scale(test_data['features'])
    train_data = test_data[~test_data['type'].mask]
    train_data['label'] = [classes.index(t) for t in train_data['type']]
    clf, sampler = train_classifier(train_data)
    logging.info('classifier trained')

    p_class = clf.predict_proba(test_data['features'])
    meta_columns = ['id', 'hostz', 'type', 'flag0', 'flag1', 'flag2']
    test_data.keep_columns(meta_columns)
    for col in ['type', 'flag0', 'flag1', 'flag2']:
        test_data[col].fill_value = ''
    for i, classname in enumerate(classes):
        test_data[classname] = p_class[:, i]
        test_data[classname].format = '%.3f'
    grouped = test_data.filled().group_by(meta_columns)
    output = grouped.groups.aggregate(np.mean)
    output.write('results.txt', format='ascii.fixed_width', overwrite=True)
    logging.info('classification results saved to results.txt')

    cnf_matrix = validate_classifier(clf, sampler, train_data)
    logging.info('validation complete')
    logging.info('finished classify.py')
