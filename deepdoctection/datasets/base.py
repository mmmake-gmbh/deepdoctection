# -*- coding: utf-8 -*-
# File: base.py

# Copyright 2021 Dr. Janis Meyer. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Module for the base class of datasets.
"""

import os
import pprint
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Union

import numpy as np

from ..dataflow import CacheData, ConcatData, CustomDataFromList, DataFlow
from ..datapoint import Image
from ..utils.logger import logger
from .dataflow_builder import DataFlowBaseBuilder
from .info import DatasetCategories, DatasetInfo, get_merged_categories


class DatasetBase(ABC):
    """
    Base class for a dataset. Requires to implementing :meth:`_categories` :meth:`_info` and :meth:`_builder` by
    yourself. These methods must return a DatasetCategories, a DatasetInfo and a DataFlow_Builder instance, which
    together give a complete description of the dataset. Compare some specific dataset cards in the :mod:`instance` .
    """

    def __init__(self) -> None:
        assert self._info() is not None, "Dataset requires at least a name defined in DatasetInfo"
        self._dataset_info = self._info()
        self._dataflow_builder = self._builder()
        self._dataflow_builder.categories = self._categories()
        self._dataflow_builder.splits = self._dataset_info.splits

        if not self.dataset_available() and self.is_built_in():
            print(
                f"Dataset {self._dataset_info.name} not locally found. Please download at {self._dataset_info.url}"
                f" and place under {self._dataflow_builder.get_workdir()}"
            )

    @property
    def dataset_info(self) -> DatasetInfo:
        """
        dataset_info
        """
        return self._dataset_info

    @property
    def dataflow(self) -> DataFlowBaseBuilder:
        """
        dataflow
        """
        return self._dataflow_builder

    @abstractmethod
    def _categories(self) -> DatasetCategories:
        """
        Construct the DatasetCategory object.
        """

        raise NotImplementedError

    @classmethod
    @abstractmethod
    def _info(cls) -> DatasetInfo:
        """
        Construct the DatasetInfo object.
        """

        raise NotImplementedError

    @abstractmethod
    def _builder(self) -> DataFlowBaseBuilder:
        """
        Construct the DataFlowBaseBuilder object. It needs to be implemented in the derived class.
        """

        raise NotImplementedError

    def dataset_available(self) -> bool:
        """
        Datasets must be downloaded and maybe unzipped manually. Checks, if the folder exists, where the dataset is
        expected.
        """
        if os.path.isdir(self._dataflow_builder.get_workdir()):
            return True
        return False

    @staticmethod
    def is_built_in() -> bool:
        """
        Returns flag to indicate if dataset is custom or built int.
        """
        return False


class _BuiltInDataset(DatasetBase, ABC):
    """
    Dataclass for built-in dataset. Do not use this it
    """

    _name: Optional[str] = None

    @staticmethod
    def is_built_in() -> bool:
        """
        Overwritten from base class
        """
        return True


class MergeDataset(DatasetBase):
    """
    A class for merging dataset ready to feed a training or an evaluation script. The dataflow builder will generate
    samples from all datasets and will exhaust if every dataflow of the merged datasets are exhausted as well. To
    guarantee flexibility it is possible to pass customized dataflows explicitly to maybe reduce the dataflow size from
    one dataset or to use different splits from different datasets.

    When yielding datapoint from :meth::build(), note that one dataset will pass all its samples successively which
    might reduce randomness for training, especially when using datasets from the same domain. Buffering all datasets
    (without loading heavy components like images) is therefore possible and the merged dataset can be shuffled.

    When the datasets are buffered are split functionality can divide the buffered samples into an train, val and test
    set.

    While the selection of categories is given by the union of all categories of all datasets, sub categories need to
    be handled with care: Only sub categories for one specific category are available provided that every dataset has
    this sub category available for this specific category. The range of sub category values again is defined as the
    range of all values from all datasets.

    **Example:**

        .. code-block:: python

            dataset_1 = get_dataset("dataset_1")
            dataset_2 = get_dataset("dataset_2")

            union_dataset = MergeDataset(dataset_1,dataset_2)
            union_dataset.buffer_datasets(split="train")     # will cache the train split of dataset_1 and dataset_2
            merge.split_datasets(ratio=0.1, add_test=False)  # will create a new split of the union.


    **Example:**

        .. code-block:: python

            dataset_1 = get_dataset("dataset_1")
            dataset_2 = get_dataset("dataset_2")

            df_1 = dataset_1.dataflow.build(max_datapoints=20)  # handle separate dataflow configs ...
            df_2 = dataset_1.dataflow.build(max_datapoints=30)

            union_dataset = MergeDataset(dataset_1,dataset_2)
            union_dataset.explicit_dataflows(df_1,df_2)   # ... and pass them explicitly. Filtering is another
                                                          # possibility
    """

    def __init__(self, *datasets: DatasetBase):
        """
        :param datasets: An arbitrary number of datasets
        """
        self.datasets = datasets
        self.dataflows: Optional[DataFlow] = None
        self.datapoint_list: Optional[List[Image]] = None
        super().__init__()

    def _categories(self) -> DatasetCategories:
        return get_merged_categories(
            *(dataset.dataflow.categories for dataset in self.datasets if dataset.dataflow.categories is not None)
        )

    @classmethod
    def _info(cls) -> DatasetInfo:
        return DatasetInfo(name="merge")

    def _builder(self) -> DataFlowBaseBuilder:
        class MergeDataFlow(DataFlowBaseBuilder):
            """
            Dataflow builder for merged datasets
            """

            def __init__(self, *dataflow_builders: DataFlowBaseBuilder):
                super().__init__("")
                self.dataflow_builders = dataflow_builders
                self.dataflows = None

            def build(self, **kwargs: Union[str, int]) -> DataFlow:
                """
                Building the dataflow of merged datasets. No argument will affect the stream if the dataflows have
                been explicitly passed. Otherwise, all kwargs will be passed to all dataflows. Note that each dataflow
                will iterate until it is exhausted. To guarantee randomness across different datasets cache all
                datapoints and shuffle them afterwards (e.g. use :meth::buffer_dataset() ).

                :param kwargs: arguments for :meth::build()
                :return: Dataflow
                """
                df_list = []
                if self.dataflows is not None:
                    logger.info("Will used dataflow from previously explicitly passed configuration")
                    return ConcatData(list(self.dataflows))

                logger.info("Will use the same build setting for all dataflows")
                for dataflow_builder in self.dataflow_builders:
                    df_list.append(dataflow_builder.build(**kwargs))
                df = ConcatData(df_list)
                return df

        builder = MergeDataFlow(*(dataset.dataflow for dataset in self.datasets))
        if self.dataflows is not None:
            builder.dataflows = self.dataflows
        return builder

    def explicit_dataflows(self, *dataflows: DataFlow) -> None:
        """
        Pass explicit dataflows for each dataset. Using several dataflow configurations for one dataset is possible as
        well. However, the number of dataflow must exceed the number of merged datasets.

        :param dataflows: An arbitrary number of dataflows
        """
        self.dataflows = dataflows
        assert len(self.datasets) <= len(self.dataflows)
        self._dataflow_builder = self._builder()
        self._dataflow_builder.categories = self._categories()

    def buffer_datasets(self, **kwargs: Union[str, int]) -> None:
        """
        Buffer datasets with given configs. If dataflows are passed explicitly it will cache their streamed output.

        :param kwargs: arguments for :meth::build()
        :return: Dataflow
        """
        df = self.dataflow.build(**kwargs)
        self.datapoint_list = CacheData(df, shuffle=True).get_cache()

    def split_datasets(self, ratio: float = 0.1, add_test: bool = True) -> None:
        """
        Split cached datasets into train/val(/test).

        :param ratio: 1-ratio will be assigned to the train split. The remaining bit will be assigned to val and test
                      split.
        :param add_test: Add a test split
        """
        assert self.datapoint_list is not None, "datasets need to be buffered before splitting"
        number_datapoints = len(self.datapoint_list)
        indices = np.random.binomial(1, ratio, number_datapoints)
        train_dataset = [self.datapoint_list[i] for i in range(number_datapoints) if indices[i] == 0]
        val_dataset = [self.datapoint_list[i] for i in range(number_datapoints) if indices[i] == 1]
        test_dataset = None

        if add_test:
            test_dataset = [dp for id, dp in enumerate(val_dataset) if id % 2]
            val_dataset = [dp for id, dp in enumerate(val_dataset) if not id % 2]

        logger.info("___________________ Number of datapoints per split ___________________")
        logger.info(
            pprint.pformat(
                {
                    "train": len(train_dataset),
                    "val": len(val_dataset),
                    "test": len(test_dataset) if test_dataset is not None else 0,
                },
                width=100,
                compact=True,
            )
        )

        class SplitDataFlow(DataFlowBaseBuilder):
            """
            Dataflow builder for splitting datasets
            """

            def __init__(self, train: List[Image], val: List[Image], test: Optional[List[Image]]):
                """
                :param train: Cached train split
                :param val: Cached val split
                :param test: Cached test split
                """
                super().__init__(location="")
                self.split_cache: Dict[str, List[Image]]
                if test is None:
                    self.split_cache = {"train": train, "val": val}
                else:
                    self.split_cache = {"train": train, "val": val, "test": test}

            def build(self, **kwargs: Union[str, int]) -> DataFlow:
                """
                Dataflow builder for merged split datasets.

                :param kwargs: Only split and max_datapoints arguments will be considered.
                :return: Dataflow
                """

                split = kwargs.get("split", "train")
                max_datapoints = int(kwargs.get("max_datapoints"))  # type: ignore

                return CustomDataFromList(self.split_cache[split], max_datapoints=max_datapoints)  # type: ignore

        self._dataflow_builder = SplitDataFlow(train_dataset, val_dataset, test_dataset)
        self._dataflow_builder.categories = self._categories()
