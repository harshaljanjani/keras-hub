from keras import ops
from moonshine_encoder import Encoder
from moonshine_preprocessor import AudioPreprocessor
from moonshine_utils import RotaryEmbedding

from keras_hub.src.api_export import keras_hub_export
from keras_hub.src.models.backbone import Backbone


@keras_hub_export("keras_hub.models.MoonshineBackbone")
class MoonshineBackbone(Backbone):
    def __init__(
        self,
        dim,
        inner_dim,
        n_head,
        enc_n_layers,
        ff_mult=4,
        enc_ff_swiglu=False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.preprocessor = AudioPreprocessor(dim)
        self.encoder = Encoder(dim, inner_dim, n_head, ff_mult, enc_ff_swiglu)
        self.dim = dim
        self.inner_dim = inner_dim
        self.n_head = n_head
        self.enc_n_layers = enc_n_layers
        self.ff_mult = ff_mult
        self.enc_ff_swiglu = enc_ff_swiglu
        self.rotary_emb = RotaryEmbedding(
            dim=self.dim // self.n_head, base=10000
        )

    def call(self, audio):
        audio_preprocessed = self.preprocessor(audio)
        seq_len = ops.shape(audio_preprocessed)[
            1
        ]  # Directly get dynamic sequence length
        rot_pos_emb = self.rotary_emb(
            ops.arange(seq_len)
        )  # Generate proper position IDs
        features = self.encoder(audio_preprocessed, rot_pos_emb)
        return features

    def get_config(self):
        config = super().get_config()
        config.update(
            {
                "dim": self.dim,
                "inner_dim": self.inner_dim,
                "n_head": self.n_head,
                "enc_n_layers": self.enc_n_layers,
                "ff_mult": self.ff_mult,
                "enc_ff_swiglu": self.enc_ff_swiglu,
            }
        )
        return config
