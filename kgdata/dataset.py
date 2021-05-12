import concurrent.futures
import itertools as it
import pathlib
import shutil
import string

import networkx as nx
import numpy as np
import pandas as pd
import tqdm

from . import decompress, download, feature, sparql, subgraph, util


@util.delegate(
    "neighbourhood",
    "all_neighbourhoods",
    "enclosing",
    "all_enclosing",
    to="subgraph_extractor",
)
class Dataset:
    def __init__(self, data):
        self.data = data

    def __len__(self):
        return len(self.data)

    @util.cached_property
    def entities(self):
        return pd.concat(
            [self.data["head"], self.data["tail"]], ignore_index=True
        ).unique()

    @util.cached_property
    def relations(self):
        return self.data["relation"].unique()

    def save(self, dest):
        self.data.to_csv(dest)

    def split(self, **kwargs):
        return Split.split(self, **kwargs)

    def load_split(self, path):
        return Split.load(self, path)

    @util.cached_property
    def subgraph_extractor(self):
        return subgraph.Extractor(self)

    def rel_dists(self):
        return feature.rel_dists(self.data)

    def to_networkx(self):
        return nx.MultiDiGraph(
            zip(self.data["head"], self.data["tail"], self.data["relation"])
        )

    @staticmethod
    def load(path):
        return Dataset(pd.read_csv(path, dtype=str))


class Split:
    def __init__(self, dataset, **partitions):
        self.dataset = dataset
        self.partitions = partitions

    def get_partition(self, name):
        return self.dataset.data.loc[self.partitions[name]]

    @util.cached_property
    def available_partitions(self):
        return list(self.partitions.keys())

    @staticmethod
    def split_idx(idx):
        train_length = round(len(idx) * 0.8)
        valid_length = (len(idx) - train_length) // 2

        train = idx[:train_length]
        valid = idx[train_length : train_length + valid_length]
        test = idx[train_length + valid_length :]

        return train, valid, test


class FB15K237Raw(Dataset):
    def __init__(self, path, split=None):
        self.path = path
        self.split = split

        if not isinstance(self.path, pathlib.Path):
            self.path = pathlib.Path(self.path)

    @util.cached_property
    def data(self):
        path = self.path / "raw"

        if not path.exists():
            self.download()

        if self.split is None:
            return pd.concat(map(pd.read_csv, path.glob("*.csv")), ignore_index=True)
        else:
            return pd.read_csv((path / self.split).with_suffix(".csv"))

    def download(self):
        compressed_path = download.download_file(
            "https://download.microsoft.com/download/8/7/0/8700516A-AB3D-4850-B4BB-805C515AECE1/FB15K-237.2.zip",
            self.path,
        )

        decompressed_path = decompress.decompress_zip(
            compressed_path, self.path, keep=True
        )

        source_dir = self.path / "Release"
        target_dir = self.path / "raw"
        target_dir.mkdir(parents=True, exist_ok=True)

        for file_name in tqdm.tqdm(
            ["train.txt", "valid.txt", "test.txt"], desc="Moving files", unit="files"
        ):
            pd.read_csv(
                source_dir / file_name,
                sep="\t",
                names=["head", "relation", "tail"],
            ).to_csv((target_dir / file_name).with_suffix(".csv"), index=False)

        shutil.rmtree(source_dir)


class FB15K237(Dataset):
    def __init__(self, path, split=None):
        self.path = path
        self.split = split

        if not isinstance(self.path, pathlib.Path):
            self.path = pathlib.Path(self.path)

    def __len__(self):
        return len(self.data)

    @util.cached_property
    def raw_dataset(self):
        return FB15K237Raw(self.path, split=self.split)

    @util.cached_property
    def data(self):
        return self.raw_dataset.data.assign(
            head=self.wikidata_labels.loc[self.raw_dataset.data["head"]].values,
            tail=self.wikidata_labels.loc[self.raw_dataset.data["tail"]].values,
        )

    @util.cached_property
    def wikidata_labels(self):
        path = self.path / "wikidata_labels.csv"

        if not path.exists():
            self.get_wikidata_labels().to_csv(path)

        return pd.read_csv(path, index_col=0)

    def get_wikidata_labels(self):
        query = (
            "SELECT ?fb ?itemLabel "
            "WHERE {{ ?item wdt:P646 ?fb. VALUES ?fb {{ {fb_ids} }} "
            "SERVICE wikibase:label {{ bd:serviceParam wikibase:language 'en'. }} }}"
        ).format(
            fb_ids=" ".join([f"'{entity}'" for entity in self.raw_dataset.entities])
        )

        result = sparql.Wikidata().query(query)

        grouped = {
            key: list(value)
            for key, value in it.groupby(
                result.bindings, lambda value: value["fb"]["value"]
            )
        }

        def reduce_group(entity):
            try:
                return list(grouped[entity])[0]["itemLabel"]["value"]
            except (IndexError, ValueError, KeyError):
                return None

        return pd.Series(
            {entity: reduce_group(entity) for entity in self.raw_dataset.entities},
            name="wikidata_label",
        )


class WN18RR(Dataset):
    def __init__(self, path, split=None):
        self.path = path
        self.split = split

        if not isinstance(self.path, pathlib.Path):
            self.path = pathlib.Path(self.path)

    @util.cached_property
    def data(self):
        if not self.path.exists():
            self.download()

        if self.split is None:
            return pd.concat(
                map(pd.read_csv, self.path.glob("*.csv")), ignore_index=True
            )
        else:
            return pd.read_csv((self.path / self.split).with_suffix(".csv"))

    def download(self):
        compressed_path = download.download_file(
            "https://data.deepai.org/WN18RR.zip", self.path
        )

        decompressed_path = decompress.decompress_zip(
            compressed_path, self.path, keep=True
        )

        for file_name in ["train.txt", "valid.txt", "test.txt"]:
            pd.read_csv(
                self.path / "WN18RR" / "text" / file_name,
                sep="\t",
                names=["head", "relation", "tail"],
            ).to_csv((self.path / file_name).with_suffix(".csv"), index=False)

        shutil.rmtree(self.path / "WN18RR")
