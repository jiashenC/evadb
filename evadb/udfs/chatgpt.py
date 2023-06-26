# coding=utf-8
# Copyright 2018-2023 EvaDB
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


import os

import pandas as pd
from retry import retry

from evadb.catalog.catalog_type import NdArrayType
from evadb.configuration.configuration_manager import ConfigurationManager
from evadb.udfs.abstract.abstract_udf import AbstractUDF
from evadb.udfs.decorators.decorators import forward, setup
from evadb.udfs.decorators.io_descriptors.data_types import PandasDataframe
from evadb.utils.generic_utils import try_to_import_openai

_VALID_CHAT_COMPLETION_MODEL = [
    "gpt-4",
    "gpt-4-0314",
    "gpt-4-32k",
    "gpt-4-32k-0314",
    "gpt-3.5-turbo",
    "gpt-3.5-turbo-0301",
]


class ChatGPT(AbstractUDF):
    @property
    def name(self) -> str:
        return "ChatGPT"

    @setup(cacheable=False, udf_type="chat-completion", batchable=True)
    def setup(
        self,
        model="gpt-3.5-turbo",
        temperature: float = 0,
    ) -> None:
        assert model in _VALID_CHAT_COMPLETION_MODEL, f"Unsupported ChatGPT {model}"
        self.model = model
        self.temperature = temperature

    @forward(
        input_signatures=[
            PandasDataframe(
                columns=["query", "content", "prompt"],
                column_types=[
                    NdArrayType.STR,
                    NdArrayType.STR,
                    NdArrayType.STR,
                ],
                column_shapes=[(1,), (1,), (None,)],
            )
        ],
        output_signatures=[
            PandasDataframe(
                columns=["response"],
                column_types=[
                    NdArrayType.STR,
                ],
                column_shapes=[(1,)],
            )
        ],
    )
    def forward(self, text_df):
        try_to_import_openai()
        import openai

        @retry(tries=6, delay=20)
        def completion_with_backoff(**kwargs):
            try:
                response = openai.ChatCompletion.create(**kwargs)
                answer = response.choices[0].message.content
            # ignore API rate limit error etc.
            except Exception as e:
                answer = f"{e}"
            return answer

        # Register API key, try configuration manager first
        openai.api_key = ConfigurationManager().get_value("third_party", "OPENAI_KEY")
        # If not found, try OS Environment Variable
        if len(openai.api_key) == 0:
            openai.api_key = os.environ.get("OPENAI_KEY", "")
        assert (
            len(openai.api_key) != 0
        ), "Please set your OpenAI API key in evadb.yml file (third_party, open_api_key) or environment variable (OPENAI_KEY)"

        queries = text_df[text_df.columns[0]]
        content = text_df[text_df.columns[0]]
        if len(text_df.columns) > 1:
            queries = text_df.iloc[:, 0]
            content = text_df.iloc[:, 1]

        prompt = None
        if len(text_df.columns) > 2:
            prompt = text_df.iloc[0, 2]

        # openai api currently supports answers to a single prompt only
        # so this udf is designed for that
        results = []

        for query, content in zip(queries, content):
            params = {
                "model": self.model,
                "temperature": self.temperature,
                "messages": [],
            }

            def_sys_prompt_message = {
                "role": "system",
                "content": prompt
                if prompt is not None
                else "You are a helpful assistant that accomplishes user tasks.",
            }

            params["messages"].append(def_sys_prompt_message)
            params["messages"].extend(
                [
                    {
                        "role": "user",
                        "content": f"Here is some context : {content}",
                    },
                    {
                        "role": "user",
                        "content": f"Complete the following task: {query}",
                    },
                ],
            )

            answer = completion_with_backoff(**params)
            results.append(answer)

        df = pd.DataFrame({"response": results})

        return df
