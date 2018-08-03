import multiprocessing
import os
import sys
from collections import defaultdict
import pickle
import time

import numpy as np
from sklearn.metrics import classification_report
from src.clustering import clustering

from src.build_data import group_data, slice_data, BATCH_SIZE


class Evaluator(object):
    def __init__(self, model, test_data_q):
        self.model = model
        self.test_data_q = test_data_q

    def fast_eval(self, test_data=False):
        Y_true = []
        Y_pred = []
        for data in self.test_data_q:
            if len(data) == 3:
                data = data[:2]
            for X, y in group_data(data, 40, BATCH_SIZE):
                pred = self.model.predict_on_batch(X)
                Y_true.append(y)
                Y_pred.append(pred)

        Y_true = np.concatenate(Y_true)
        Y_true = Y_true.flatten()
        Y_pred = np.concatenate(Y_pred)
        Y_pred = Y_pred.flatten()
        Y_pred = np.round(Y_pred).astype(int)

        return classification_report(Y_true, Y_pred, digits=3)

    def write_results(self, df, dest_path):
        n_files = len(self.test_data_q)
        print("# files: %d" % n_files)
        for i, data in enumerate(self.test_data_q):
            if i == n_files:
                break
            X, y, index_map = data
            pred = []
            for X, y in group_data([X, y], 40, batch_size=BATCH_SIZE):
                pred.append(self.model.predict_on_batch(X))
            pred = np.concatenate(pred)
            pred = pred.flatten()

            pair_results = {}
            for key in index_map:
                pair_results[(key[1], key[2])] = pred[index_map[key]]
            locs, clusters, _ = clustering(pair_results)
            doc_id = key[0]
            length = len(df.loc[df.doc_id == doc_id])

            # print("Saving %s results..." % doc_id)
            sys.stdout.write("Saving results %d / %d\r" % (i + 1, n_files))
            sys.stdout.flush()
            corefs = ['-' for i in range(length)]
            for loc, cluster in zip(locs, clusters):
                start, end = loc
                if corefs[start] == '-':
                    corefs[start] = '(' + str(cluster)
                else:
                    corefs[start] += '|(' + str(cluster)

                if corefs[end] == '-':
                    corefs[end] = str(cluster) + ')'
                elif start == end:
                    corefs[end] += ')'
                else:
                    corefs[end] += '|' + str(cluster) + ')'
            with open(os.path.join(dest_path, doc_id.split('/')[-1]), 'w') as f:
                f.write('#begin document (%s);\n' % doc_id)
                for coref in corefs:
                    f.write(doc_id + '\t' + coref +'\n')
                f.write('\n#end document\n')
        print("Completed saving results!")


class TriadEvaluator(object):
    def __init__(self, model, test_input_gen, file_batch=10):
        self.model = model
        # self.test_data_gen = test_data_gen
        # self.test_input_gen = test_data_gen.generate_triad_input(file_batch=file_batch, threads=1)
        self.test_input_gen = test_input_gen
        self.data_q_store = multiprocessing.Queue(maxsize=5)
        # self.data_available = False

    # def fill_q_store(self):
    #     print("evaluator data filler started...")
    #     self.data_available = True
    #     while True:
    #         if not self.data_q_store.full():
    #             self.data_q_store.put(self.test_input_gen.next())
    #         else:
    #             time.sleep(1)
    #     self.data_available = False

    def fast_eval(self):
        """Fast evaluation from a subset of test files
           Scores are based on pairs only
        """
        # assert self.data_available
        Y_true = []
        Y_pred = []
        # test_data_q = self.data_q_store.get()
        test_data_q = next(self.test_input_gen)
        for data in test_data_q:
            if len(data) == 3:
                data = data[:2]
            for X, y in slice_data(data, 100):
                if y.shape[-1] == 3:
                    y = y[:, 1:]
                pred = self.model.predict(X) # (group_size, 3)
                Y_true.append(y)
                Y_pred.append(pred)

        Y_true = np.concatenate(Y_true)
        Y_true = Y_true.flatten()
        Y_pred = np.concatenate(Y_pred)
        Y_pred = Y_pred.flatten()
        Y_pred = np.round(Y_pred).astype(int)

        return classification_report(Y_true, Y_pred, digits=3)

    def write_results(self, df, dest_path, n_iterations, save_dendrograms=True, clustering_only=False, compute_linkage=False):
        """Perform evaluation on all test data, write results"""
        # assert self.data_available
        print("# files: %d" % n_iterations)

        all_pairs_true = []
        all_pairs_pred = []
        processed_docs = set([])
        doc_ids = df.doc_id.unique()
        i = n_iterations
        # i = n_iterations -2 #  ignore 2 large files for fast evaluation
        t = 2.5

        while i > 0:
            if not clustering_only:
                test_data_q = next(self.test_input_gen)
                assert len(test_data_q) == 1  # only process one file
                if not test_data_q[0]:
                    i -= 1
                    continue
                X, y, index_map = test_data_q[0]
                if y.shape[-1] == 3:
                    y = y[:, 1:]

                doc_id = list(index_map.keys())[0][0]  # python3 does not support keys() as a list
                if doc_id in processed_docs:
                    # print("%s already processed before!" % doc_id)
                    time.sleep(10)
                    continue
                processed_docs.add(doc_id)
                pred = []
                for X, _ in slice_data([X, y], 50):  # do this to avoid very large batches
                    pred.append(self.model.predict(X))
                pred = np.concatenate(pred)
                pred = np.reshape(pred, [-1, 2])  # in case there are batches

                true = np.reshape(y, [-1, 2])

                pair_results = defaultdict(list)
                pair_true = {}
                for key in index_map:
                    pair_results[(key[2], key[3])].append(pred[index_map[key]][0])
                    pair_results[(key[1], key[3])].append(pred[index_map[key]][1])

                    pair_true[(key[2], key[3])] = true[index_map[key]][0]
                    pair_true[(key[1], key[3])] = true[index_map[key]][1]

                # save raw scores
                pickle.dump(pair_results, open(os.path.join(dest_path, 'raw_scores', doc_id.split('/')[-1]+'results.pkl'), 'wb'))

                pair_results_mean = {}
                for key, value in pair_results.items():
                    # mean_value = TriadEvaluator.top_n_mean(value, 0)
                    # mean_value = TriadEvaluator.bottom_n_mean(value, 3)
                    mean_value = TriadEvaluator.last_n_values(value, 2)
                    pair_results_mean[key] = mean_value
                    all_pairs_pred.append(mean_value)
                    all_pairs_true.append(pair_true[key])

                locs, clusters, linkage = clustering(pair_results_mean, binarize=False, t=t)
                _, clusters_true, linkage_true = clustering(pair_true, binarize=False, t=t)

                clusters = TriadEvaluator.remove_singletons(clusters)

                if save_dendrograms:
                    np.save(os.path.join(dest_path, 'linkages', doc_id.split('/')[-1]+'.npy'), linkage)
                    with open(os.path.join(dest_path, 'linkages', doc_id.split('/')[-1] + '-locs.pkl'), 'wb') as f:
                        pickle.dump(locs, f)
                    # np.save(os.path.join(dest_path, 'true-linkages', doc_id.split('/')[-1] + '.npy'), linkage_true)

            else:  # clustering only
                from scipy.cluster.hierarchy import inconsistent, fcluster
                doc_id = doc_ids[i - 1]
                if doc_id.split('/')[-1] == 'wsj_2390':
                    print("file not found:", doc_id)
                    i -= 1
                    continue

                if compute_linkage:
                    pair_results = pickle.load(open(os.path.join(dest_path, 'raw_scores', doc_id.split('/')[-1]+'results.pkl'), 'rb'))

                    pair_results_mean = {}
                    for key, value in pair_results.items():
                        # mean_value = TriadEvaluator.top_n_mean(value, 0)
                        # mean_value = TriadEvaluator.bottom_n_mean(value, 3)
                        mean_value = TriadEvaluator.last_n_values(value, 3)
                        pair_results_mean[key] = mean_value

                    locs, clusters, linkage = clustering(pair_results_mean, binarize=False, t=t)
                    np.save(os.path.join(dest_path, 'linkages', doc_id.split('/')[-1] + '.npy'), linkage)


                else:
                    linkage = np.load(os.path.join(dest_path, 'linkages', doc_id.split('/')[-1] + '.npy'))
                    with open(os.path.join(dest_path, 'linkages', doc_id.split('/')[-1]+'-locs.pkl'), 'rb') as f:
                        locs = pickle.load(f)

                    depth = 6
                    criterion = 'distance'
                    R = None
                    # criterion = 'inconsistent'
                    # R = inconsistent(linkage, d=depth)
                    clusters = fcluster(linkage, criterion=criterion, depth=depth, R=R, t=t)

                clusters = TriadEvaluator.remove_singletons(clusters)

            doc_df = df.loc[df.doc_id == doc_id]
            length = len(doc_df)
            # print("Saving %s results..." % doc_id)
            sys.stdout.write("Saving results %d / %d\r" % (n_iterations - i + 1, n_iterations))
            sys.stdout.flush()
            corefs = ['-' for _ in range(length)]
            for loc, cluster in zip(locs, clusters):

                if cluster == -1:  # singletons
                    continue

                start, end = loc
                if corefs[start] == '-':
                    corefs[start] = '(' + str(cluster)
                else:
                    corefs[start] += '|(' + str(cluster)

                if corefs[end] == '-':
                    corefs[end] = str(cluster) + ')'
                elif start == end:
                    corefs[end] += ')'
                else:
                    corefs[end] += '|' + str(cluster) + ')'
            with open(os.path.join(dest_path, 'responses', doc_id.split('/')[-1]), 'w') as f:
                f.write('#begin document (%s);\n' % doc_id)
                for loc, coref in enumerate(corefs):
                    word = doc_df.iloc[loc].word
                    f.write(doc_id + '\t' + word + '\t' + coref + '\n')
                f.write('\n#end document\n')

            i -= 1

        print("Completed saving results!")
        if not clustering_only:
            print("Pairwise evaluation:")
            print("True histogram", np.histogram(all_pairs_true, bins=4))
            print("Prediction histogram", np.histogram(all_pairs_pred, bins=4))
            print(classification_report(all_pairs_true, np.round(all_pairs_pred), digits=3))

    @staticmethod
    def last_n_values(values, n):
        if len(values) >= n:
            value = np.mean(values[-n:])
        else:
            value = np.mean(values)
        return value

    @staticmethod
    def top_n_mean(values, n):
        if n >= 1:
            values.sort(reverse=True)
            if len(values) >= n:
                values = values[:n]
        return np.mean(values)

    @staticmethod
    def median(values):
        values.sort(reverse=True)
        return values[len(values)/2]

    @staticmethod
    def nonlinear_mean(values):
        return np.mean(np.round(values))

    @staticmethod
    def bottom_n_mean(values, n):
        if n >= 1:
            values.sort()
            if len(values) >= n:
                values = values[:n]
        return np.mean(values)

    @staticmethod
    def remove_singletons(clusters):
        counter = defaultdict(int)
        for item in clusters:
            counter[item] += 1
        for i, item in enumerate(clusters):
            if counter[item] == 1:
                clusters[i] = -1  # use -1 as a special value for singletons
        return clusters
