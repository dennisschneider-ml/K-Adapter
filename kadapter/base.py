from . import util

from transformers import BertConfig
from transformers.models.bert.modeling_bert import BertEncoder
import torch
from torch import nn, Tensor


class AdapterLayer(nn.Module):
    """
    A single adapter layer injected at a certain hidden layer of the base-model.
    """
    
    def __init__(self, basemodel_hidden_dim: int, hidden_dimension: int, initializer_range: float, **bert_config_params):
        super().__init__()
        self.initializer_range = initializer_range
        self.down_project = nn.Linear(basemodel_hidden_dim, hidden_dimension)
        bert_config = BertConfig(**bert_config_params)
        self.encoder = BertEncoder(bert_config)
        self.up_project = nn.Linear(hidden_dimension, basemodel_hidden_dim)

    def forward(self, input_features: Tensor) -> Tensor:
        down_projected = self.down_project(input_features)
        encoder_outputs = self.encoder(down_projected)
        up_projected = self.up_project(encoder_outputs[0])
        # skip connection
        return input_features + up_projected

    def init_weights(self):
        # original
        for m in self.modules():
            if isinstance(m, nn.Linear):
                m.weight.data.uniform_(-self.initializer_range, self.initializer_range)
                if not m.bias is None:
                    m.bias.data.zero_()
            elif isinstance(m, nn.Embedding):
                m.weight.data.uniform_(-self.initializer_range, self.initializer_range)


class Adapter(nn.Module):
    """
    A single Adapter-module which can be trained isolated.
    """

    def __init__(self, model: nn.Module, injection_layers: str, skip_layers=3, **kwargs):
        super().__init__()
        # add injection-hooks to the base-model
        self.basemodel = util.FeatureExtractor(model, injection_layers)
        self.skip_layers = skip_layers
        self.adapter_layers = [AdapterLayer(model.config.hidden_size, **kwargs) for _ in injection_layers]

    def forward(self, inputs):
        base_output = self.basemodel(inputs)
        base_hidden_injections = self.basemodel.features()
        adapter_outputs = []
        for adapter_module, base_hidden_features in zip(self.adapter_layers, base_hidden_injections):
            prev_adapter_output = adapter_outputs[-1] if adapter_outputs else torch.zeros(base_output.last_hidden_state.shape)
            fusion_input = base_hidden_features + prev_adapter_output
            adapter_outputs.append(adapter_module(fusion_input))

            if (self.skip_layers > 0) and (len(adapter_outputs) % self.skip_layers == 0):
                adapter_outputs[-1] += adapter_outputs[int(len(adapter_outputs) // self.adapter_skip_layers)]

        return adapter_outputs[-1], base_output.last_hidden_state


class AdapterFactory:
    """
    Initialize Adapter from configuration.
    """
    
    def __init__(self, model):
        self.model = model
    
    def __call__(self, **kwargs):
        return Adapter(self.model, **kwargs)
