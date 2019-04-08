from collections import defaultdict, OrderedDict
import pickle as pickle
from multiprocessing import Process
from netquery.graph import Query
import torch
from torch.utils.data import Dataset, DataLoader
from .graph import _reverse_relation
import numpy as np

def load_queries(data_file, keep_graph=False):
    raw_info = pickle.load(open(data_file, "rb"))
    return [Query.deserialize(info, keep_graph=keep_graph) for info in raw_info]

def load_queries_by_formula(data_file):
    raw_info = pickle.load(open(data_file, "rb"))
    queries = defaultdict(lambda : defaultdict(list))
    for raw_query in raw_info:
        query = Query.deserialize(raw_query)
        queries[query.formula.query_type][query.formula].append(query)
    return queries

def load_queries_by_type(data_file, keep_graph=True):
    raw_info = pickle.load(open(data_file, "rb"))
    queries = defaultdict(list)
    for raw_query in raw_info:
        query = Query.deserialize(raw_query, keep_graph=keep_graph)
        queries[query.formula.query_type].append(query)
    return queries


def load_test_queries_by_formula(data_file):
    raw_info = pickle.load(open(data_file, "rb"))
    queries = {"full_neg" : defaultdict(lambda : defaultdict(list)), 
            "one_neg" : defaultdict(lambda : defaultdict(list))}
    for raw_query in raw_info:
        neg_type = "full_neg" if len(raw_query[1]) > 1 else "one_neg"
        query = Query.deserialize(raw_query)
        queries[neg_type][query.formula.query_type][query.formula].append(query)
    return queries

def sample_clean_test(graph_loader, data_dir):
    train_graph = graph_loader()
    test_graph = graph_loader()
    test_edges = load_queries(data_dir + "/test_edges.pkl")
    val_edges = load_queries(data_dir + "/val_edges.pkl")
    train_graph.remove_edges([(q.target_node, q.formula.rels[0], q.anchor_nodes[0]) for q in test_edges+val_edges])
    test_queries_2 = test_graph.sample_test_queries(train_graph, ["2-chain", "2-inter"], 9000, 1)
    test_queries_2.extend(test_graph.sample_test_queries(train_graph, ["2-chain", "2-inter"], 1000, 1000))
    val_queries_2 = test_graph.sample_test_queries(train_graph, ["2-chain", "2-inter"], 10, 900)
    val_queries_2.extend(test_graph.sample_test_queries(train_graph, ["2-chain", "2-inter"], 100, 1000))
    val_queries_2 = list(set(val_queries_2)-set(test_queries_2))
    print(len(val_queries_2))
    test_queries_3 = test_graph.sample_test_queries(train_graph, ["3-chain", "3-inter", "3-inter_chain", "3-chain_inter"], 9000, 1)
    test_queries_3.extend(test_graph.sample_test_queries(train_graph, ["3-chain", "3-inter", "3-inter_chain", "3-chain_inter"], 1000, 1000))
    val_queries_3 = test_graph.sample_test_queries(train_graph, ["3-chain", "3-inter", "3-inter_chain", "3-chain_inter"], 900, 1)
    val_queries_3.extend(test_graph.sample_test_queries(train_graph, ["3-chain", "3-inter", "3-inter_chain", "3-chain_inter"], 100, 1000))
    val_queries_3 = list(set(val_queries_3)-set(test_queries_3))
    print(len(val_queries_3))
    pickle.dump([q.serialize() for q in test_queries_2], open(data_dir + "/test_queries_2-newclean.pkl", "wb"), protocol=pickle.HIGHEST_PROTOCOL)
    pickle.dump([q.serialize() for q in test_queries_3], open(data_dir + "/test_queries_3-newclean.pkl", "wb"), protocol=pickle.HIGHEST_PROTOCOL)
    pickle.dump([q.serialize() for q in val_queries_2], open(data_dir + "/val_queries_2-newclean.pkl", "wb"), protocol=pickle.HIGHEST_PROTOCOL)
    pickle.dump([q.serialize() for q in val_queries_3], open(data_dir + "/val_queries_3-newclean.pkl", "wb"), protocol=pickle.HIGHEST_PROTOCOL)

        
def clean_test(train_queries, test_queries):
    for query_type in train_queries:
        train_set = set(train_queries[query_type])
        test_queries[query_type] = [q for q in test_queries[query_type] if not q in train_set]
    return test_queries

def parallel_sample_worker(pid, num_samples, graph, data_dir, is_test, test_edges):
    if not is_test:
        graph.remove_edges([(q.target_node, q.formula.rels[0], q.anchor_nodes[0]) for q in test_edges])
    print("Running worker", pid)
    queries_2 = graph.sample_queries(2, num_samples, 100 if is_test else 1, verbose=True)
    queries_3 = graph.sample_queries(3, num_samples, 100 if is_test else 1, verbose=True)
    print("Done running worker, now saving data", pid)
    pickle.dump([q.serialize() for q in queries_2], open(data_dir + "/queries_2-{:d}.pkl".format(pid), "wb"), protocol=pickle.HIGHEST_PROTOCOL)
    pickle.dump([q.serialize() for q in queries_3], open(data_dir + "/queries_3-{:d}.pkl".format(pid), "wb"), protocol=pickle.HIGHEST_PROTOCOL)

def parallel_sample(graph, num_workers, samples_per_worker, data_dir, test=False, start_ind=None):
    if test:
        print("Loading test/val data..")
        test_edges = load_queries(data_dir + "/test_edges.pkl")
        val_edges = load_queries(data_dir + "/val_edges.pkl")
    else:
        test_edges = []
        val_edges = []
    proc_range = list(range(num_workers)) if start_ind is None else list(range(start_ind, num_workers+start_ind))
    procs = [Process(target=parallel_sample_worker, args=[i, samples_per_worker, graph, data_dir, test, val_edges+test_edges]) for i in proc_range]
    for p in procs:
        p.start()
    for p in procs:
        p.join() 
    queries_2 = []
    queries_3 = []
    for i in range(num_workers):
        new_queries_2 = load_queries(data_dir+"/queries_2-{:d}.pkl".format(i), keep_graph=True)
        queries_2.extend(new_queries_2)
        new_queries_3 = load_queries(data_dir+"/queries_3-{:d}.pkl".format(i), keep_graph=True)
        queries_3.extend(new_queries_3)
    return queries_2, queries_3


class QueryDataset(Dataset):
    """A dataset for queries of a specific type, e.g. 1-chain.
    The dataset contains queries for formulas of different types, e.g.
    200 queries of type (('protein', '0', 'protein')),
    500 queries of type (('protein', '0', 'function')).
    (note that these queries are of type 1-chain).

    Args:
        queries (dict): maps formulas (graph.Formula) to query instances
            (list of graph.Query?)
    """
    def __init__(self, queries, *args, **kwargs):
        self.queries = queries
        self.num_formula_queries = OrderedDict()
        for form, form_queries in queries.items():
            self.num_formula_queries[form] = len(form_queries)
        self.num_queries = sum(self.num_formula_queries.values())
        self.max_num_queries = max(self.num_formula_queries.values())

    def __len__(self):
        return self.max_num_queries

    def __getitem__(self, index):
        return index

    def collate_fn(self, idx_list):
        # Select a formula type (e.g. ('protein', '0', 'protein'))
        # with probability proportional to the number of queries of that
        # formula type
        counts = np.array(list(self.num_formula_queries.values()))
        probs = counts / float(self.num_queries)
        formula_index = np.argmax(np.random.multinomial(1, probs))
        formula = list(self.num_formula_queries.keys())[formula_index]

        n = self.num_formula_queries[formula]
        # Assume sorted idx_list
        min_idx, max_idx = idx_list[0], idx_list[-1]

        start = min_idx % n
        end = min((max_idx + 1) % n, n)
        end = n if end <= start else end
        queries = self.queries[formula][start:end]

        return formula, queries


from torch_geometric.data import Data, Batch

QUERY_EDGE_INDICES = {'1-chain': [[0],
                                  [1]],
                      '2-chain': [[0, 2],
                                  [2, 1]],
                      '3-chain': [[0, 3, 2],
                                  [3, 2, 1]],
                      '2-inter': [[0, 1],
                                  [2, 2]],
                      '3-inter': [[0, 1, 2],
                                  [3, 3, 3]],
                      '3-inter_chain': [[0, 1, 3],
                                        [2, 3, 2]],
                      '3-chain_inter': [[0, 1, 3],
                                        [3, 3, 2]]}

QUERY_EDGE_LABEL_IDX = {'1-chain': [0],
                        '2-chain': [1, 0],
                        '3-chain': [2, 1, 0],
                        '2-inter': [0, 1],
                        '3-inter': [0, 1, 2],
                        '3-inter_chain': [0, 2, 1],
                        '3-chain_inter': [1, 2, 0]}

VARIABLE_NODE_IDX = {'1-chain': [0],
                     '2-chain': [0, 2],
                     '3-chain': [0, 2, 4],
                     '2-inter': [0],
                     '3-inter': [0],
                     '3-chain_inter': [0, 2],
                     '3-inter_chain': [0, 3]}

class RGCNQueryDataset(Dataset):
    """A dataset for queries of a specific type, e.g. 1-chain.
    The dataset contains queries for formulas of different types, e.g.
    200 queries of type (('protein', '0', 'protein')),
    500 queries of type (('protein', '0', 'function')).
    (note that these queries are of type 1-chain).

    Args:
        queries (dict): maps formulas (graph.Formula) to query instances
            (list of graph.Query?)
    """
    def __init__(self, queries, enc_dec):
        mode_ids = enc_dec.mode_ids
        rel_ids = enc_dec.rel_ids

        example_formula = next(iter(queries))
        query_type = example_formula.query_type
        n_anchors = len(example_formula.anchor_modes)
        n_vars = len(VARIABLE_NODE_IDX[query_type])
        n_queries = sum(map(len, queries.values()))
        n_rels = len(QUERY_EDGE_LABEL_IDX[query_type])

        self.anchor_nodes = torch.empty(n_queries, n_anchors, dtype=torch.long)
        self.var_nodes = torch.empty(n_queries, n_vars, dtype=torch.long)
        self.edge_types = torch.empty(n_queries, n_rels, dtype=torch.long)
        self.target_nodes = torch.empty(n_queries, dtype=torch.long)
        self.graph_num_nodes = torch.tensor(enc_dec.num_entities)
        self.neg_nodes = -1 * torch.ones(n_queries, dtype=torch.long)
        self.hard_neg = -1 * torch.ones(n_queries, dtype=torch.long)

        i = 0
        for formula, form_queries in queries.items():
            all_nodes = formula.get_nodes()
            var_idx = VARIABLE_NODE_IDX[formula.query_type]
            var_ids = torch.tensor([mode_ids[all_nodes[i]] for i in var_idx],
                               dtype=torch.long)

            rels = formula.get_rels()
            rel_idx = QUERY_EDGE_LABEL_IDX[formula.query_type]
            edge_type = [rel_ids[_reverse_relation(rels[i])] for i in rel_idx]
            edge_type = torch.tensor(edge_type, dtype=torch.long)

            for query in form_queries:
                self.anchor_nodes[i] = torch.tensor(query.anchor_nodes, dtype=torch.long)
                self.var_nodes[i] = var_ids
                self.edge_types[i] = edge_type
                self.target_nodes[i] = query.target_node

                if query.neg_samples is not None and len(query.neg_samples) > 0:
                    self.neg_nodes[i] = query.neg_samples[0]
                if query.hard_neg_samples is not None and len(query.hard_neg_samples) > 0:
                    self.hard_neg[i] = query.hard_neg_samples[0]

                i += 1

        edge_index = QUERY_EDGE_INDICES[query_type]
        self.edge_index = torch.tensor(edge_index, dtype=torch.long)

    def __getitem__(self, index):
        return index

    def __len__(self):
        return self.anchor_nodes.shape[0]

    def collate_fn(self, idx_list):
        batch_size = len(idx_list)
        anchor_ids = self.anchor_nodes[idx_list]
        var_ids = self.var_nodes[idx_list]
        #node_ids = torch.cat((anchor_ids, var_ids), dim=-1).reshape(-1)

        n_anchors, n_vars = anchor_ids.shape[1], var_ids.shape[1]
        n_nodes = n_anchors + n_vars

        # Create sparse block adjacency matrix
        repeat_edge_idx = self.edge_index.unsqueeze(dim=0).repeat(batch_size, 1, 1)
        offsets = torch.arange(start=0, end=n_nodes*batch_size, step=n_nodes).view(-1, 1, 1)

        edge_index = repeat_edge_idx + offsets
        # Shape: [batch_size, 2, n_edges]

        edge_index = edge_index.transpose(-1, -2).reshape(-1, 2).t().contiguous()
        # Shape: [2, batch_size * n_edges]

        edge_types = self.edge_types[idx_list].reshape(-1)

        batch_idx = torch.arange(batch_size).expand(n_nodes, -1).t().reshape(-1).contiguous()

        targets = self.target_nodes[idx_list]

        hard_negatives = self.hard_neg[idx_list]

        if n_nodes == 2:
            neg_targets = torch.randint(self.graph_num_nodes, (batch_size,), dtype=torch.long)
        else:
            neg_targets = self.neg_nodes[idx_list]

        return (anchor_ids, var_ids, edge_index, edge_types, batch_idx,
                targets, neg_targets, hard_negatives)

    @staticmethod
    def get_query_graph(formula, queries, rel_ids, mode_ids):
        batch_size = len(queries)
        n_anchors = len(formula.anchor_modes)

        anchor_ids = np.empty([batch_size, n_anchors]).astype(np.int)
        # First rows of x contain embeddings of all anchor nodes
        for i, anchor_mode in enumerate(formula.anchor_modes):
            anchors = [q.anchor_nodes[i] for q in queries]
            anchor_ids[:, i] = anchors

        # The rest of the rows contain generic mode embeddings for variable nodes
        all_nodes = formula.get_nodes()
        var_idx = VARIABLE_NODE_IDX[formula.query_type]
        var_ids = np.array([mode_ids[all_nodes[i]] for i in var_idx],
                           dtype=np.int)
        var_ids = np.tile(var_ids, (batch_size, 1))

        edge_index = QUERY_EDGE_INDICES[formula.query_type]
        edge_index = torch.tensor(edge_index, dtype=torch.long)

        rels = formula.get_rels()
        rel_idx = QUERY_EDGE_LABEL_IDX[formula.query_type]
        edge_type = [rel_ids[_reverse_relation(rels[i])] for i in rel_idx]
        edge_type = torch.tensor(edge_type, dtype=torch.long)

        edge_data = Data(edge_index=edge_index)
        edge_data.edge_type = edge_type
        graph = Batch.from_data_list([edge_data for i in range(batch_size)])

        return (torch.tensor(anchor_ids, dtype=torch.long),
                torch.tensor(var_ids, dtype=torch.long),
                graph.edge_index,
                graph.edge_type,
                graph.batch)


def make_data_iterator(data_loader):
    iterator = iter(data_loader)
    while True:
        try:
            yield next(iterator)
        except StopIteration:
            iterator = iter(data_loader)
            continue


def get_queries_iterator(queries, batch_size, enc_dec=None):
    dataset = RGCNQueryDataset(queries, enc_dec)
    loader = DataLoader(dataset, batch_size, shuffle=True,
                        collate_fn=dataset.collate_fn, num_workers=1)
    return make_data_iterator(loader)


if __name__ == '__main__':
    queries = {('protein','0','protein'): ['a' + str(i) for i in range(10)],
               ('protein', '0', 'function'): ['b' + str(i) for i in range(20)],
               ('function', '0', 'function'): ['c' + str(i) for i in range(30)]}

    iterator = get_queries_iterator(queries, batch_size=4)

    for i in range(50):
        batch = next(iterator)
        print(batch)
