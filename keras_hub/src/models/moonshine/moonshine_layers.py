import keras


@keras.saving.register_keras_serializable(package="keras_hub")
class MoonshineInvFreqInitializer(keras.initializers.Initializer):
    """
    Moonshine inverse frequency initializer.

    Initializes weights for computing inverse frequencies used in rotary
    embeddings. It generates a tensor of inverse frequency values based on the
    specified dimension and base.

    Args:
        dim: int, The dimensionality for which to compute inverse frequencies.
        This should be an even number representing half the number of features.
        max_position_embeddings: int, Maximum sequence length the model will
        process. Used to control the scale of position embeddings.
        base: float, The exponential base value used in computing the inverse
        frequencies. Higher values produce longer wavelengths. Defaults to
        10000.
        scaling_factor: float, Multiplier applied to the inverse frequencies to
        control the scale of the embeddings. Defaults to 1.0.

    Returns:
        A tensor of shape (dim,) containing the scaled inverse frequency values.
    """

    def __init__(
        self, dim, max_position_embeddings, base=10000, scaling_factor=1.0
    ):
        self.dim = dim
        self.max_position_embeddings = max_position_embeddings
        self.base = base
        self.scaling_factor = scaling_factor

    def __call__(self, shape, dtype=None, **kwargs):
        if dtype is None:
            dtype = "float32"

        inv_freq = 1.0 / (
            self.base ** (keras.ops.arange(0, self.dim, dtype=dtype) / self.dim)
        )
        return inv_freq * self.scaling_factor

    def get_config(self):
        return {
            "dim": self.dim,
            "max_position_embeddings": self.max_position_embeddings,
            "base": self.base,
            "scaling_factor": self.scaling_factor,
        }


@keras.saving.register_keras_serializable(package="keras_hub")
class MoonshineRotaryEmbedding(keras.layers.Layer):
    """
    Moonshine rotary embedding layer.

    Computes rotary positional embeddings using precomputed inverse frequencies.
    The layer stores the inverse frequency weights as a non-trainable parameter
    and uses them to compute sinusoidal embeddings based on input positions.

    Args:
        dim: int, The head dimension for which rotary embeddings are computed.
        This determines the feature dimensionality of the embeddings.
        max_position_embeddings: int, Maximum sequence length the model will
        process. Controls scaling of position embeddings. Defaults to 2048.
        base: float, Base value for computing inverse frequencies. Higher values
        produce longer wavelengths. Defaults to 10000.
        scaling_factor: float, Multiplier applied to inverse frequencies to
        control the scale of position embeddings. Defaults to 1.0.
        partial_rotary_factor: float, Proportion of head dimensions that will
        use rotary embeddings. Controls the balance between rotary and
        non-rotary components. Defaults to 0.62.
        dtype: string or `keras.mixed_precision.DTypePolicy`, optional, The
        dtype to use for model computations and weights. Defaults to None.
        **kwargs: Additional keyword arguments passed to the parent class.

    Returns:
        A tensor containing rotary embeddings reshaped to the appropriate output
        dimensions based on input positions.
    """

    def __init__(
        self,
        dim,
        max_position_embeddings=2048,
        base=10000,
        scaling_factor=1.0,
        partial_rotary_factor=0.62,
        dtype=None,
        **kwargs,
    ):
        super().__init__(dtype=dtype, **kwargs)
        self.dim = dim
        self.max_position_embeddings = max_position_embeddings
        self.base = base
        self.scaling_factor = scaling_factor
        self.partial_rotary_factor = partial_rotary_factor

    def build(self, input_shape):
        # Create and track the non-trainable weight immediately.
        head_dim = self.dim
        rotary_dim = int(head_dim * self.partial_rotary_factor)
        rotary_dim = (rotary_dim // 2) * 2
        rotary_dim = rotary_dim // 2

        self.inv_freq = self.add_weight(
            name="inv_freq",
            shape=(rotary_dim,),
            initializer=MoonshineInvFreqInitializer(
                rotary_dim,
                self.max_position_embeddings,
                self.base,
                self.scaling_factor,
            ),
            trainable=False,
            dtype=self.dtype,
        )
        self.built = True

    def call(self, t):
        # Note: Cannot compute inv_freq on the fly here instead of storing it as
        # a weight. Causes NoneType error.
        t_cast = keras.ops.cast(t, keras.ops.dtype(self.inv_freq))
        freqs = keras.ops.einsum("i,j->ij", t_cast, self.inv_freq)
        emb = keras.ops.stack((freqs, freqs), axis=-1)
        shape_list = list(keras.ops.shape(emb))
        shape_list[-2:] = [-1]
        return keras.ops.reshape(emb, shape_list)

    def get_config(self):
        config = super().get_config()
        config.update(
            {
                "dim": self.dim,
                "max_position_embeddings": self.max_position_embeddings,
                "base": self.base,
                "scaling_factor": self.scaling_factor,
                "partial_rotary_factor": self.partial_rotary_factor,
                "dtype": self.dtype,
            }
        )
        return config


class MoonshineArange(keras.layers.Layer):
    """
    Moonshine arange layer.

    Args:
    inputs: Tensor, The input tensor containing the end value for the range.
    This will be squeezed to extract a scalar value.

    Returns:
        A 1-D tensor containing integer values from 0 to the scalar derived from
        input.
    """

    def __init__(self, dtype=None, **kwargs):
        super().__init__(dtype=dtype, **kwargs)

    def call(self, inputs):
        return keras.ops.arange(
            keras.ops.squeeze(inputs), dtype=self.compute_dtype
        )

    def compute_output_spec(self, input_shape):
        return keras.KerasTensor((None,), dtype=self.compute_dtype)


@keras.saving.register_keras_serializable(package="keras_hub")
class MoonshineSwiGLU(keras.layers.Layer):
    """
    Moonshine SwiGLU feedforward layer.

    Implements a SwiGLU feedforward activation block. The layer applies a dense
    projection that outputs 2 * feedforward_expansion_factor * hidden_dim units,
    splits the output into two halves, applies a SiLU activation to the gating
    half, multiplies the two halves elementwise, and projects the result back to
    hidden_dim.

    Args:
        hidden_dim: int, The input and output dimensionality of the layer.
        Controls the width of the network representations.
        feedforward_expansion_factor: int, The multiplicative factor for the
        intermediate dense layer. Determines how much the representation is
        expanded internally before projection back to hidden_dim.
        dtype: string or `keras.mixed_precision.DTypePolicy`, optional, The
        dtype to use for model computations and weights. Defaults to None.
        **kwargs: Additional keyword arguments passed to the parent class.

    Returns:
        A tensor with the same last dimension as the input (hidden_dim) after
        applying the SwiGLU feedforward transformation.
    """

    def __init__(
        self,
        hidden_dim,
        feedforward_expansion_factor,
        dtype=None,
        **kwargs,
    ):
        super().__init__(dtype=dtype, **kwargs)
        self.hidden_dim = hidden_dim
        self.feedforward_expansion_factor = feedforward_expansion_factor
        # First dense layer produces 2 * feedforward_expansion_factor *
        # hidden_dim outputs.
        self.dense_1 = keras.layers.Dense(
            hidden_dim * feedforward_expansion_factor * 2,
            use_bias=True,
            name="dense_1",
            dtype=self.dtype,
        )
        # Activation layer using "silu" (Swish activation).
        self.activation = keras.layers.Activation(
            "silu",
            name="activation",
            dtype=self.dtype,
        )
        # Second dense layer projects back to hidden_dim.
        self.dense_2 = keras.layers.Dense(
            hidden_dim,
            use_bias=True,
            name="dense_2",
            dtype=self.dtype,
        )

    def build(self, input_shape):
        super().build(input_shape)
        # Build the first dense layer using the original input shape.
        self.dense_1.build(input_shape)
        # After dense_1, the output shape becomes: (..., 2 *
        # feedforward_expansion_factor * hidden_dim).
        # When splitting, each part will have shape (...,
        # feedforward_expansion_factor * hidden_dim).
        new_input_shape = list(input_shape)
        new_input_shape[-1] = (
            self.hidden_dim * self.feedforward_expansion_factor
        )
        self.dense_2.build(tuple(new_input_shape))

    def call(self, inputs):
        x = self.dense_1(inputs)
        x1, gate = keras.ops.split(x, 2, axis=-1)
        x = x1 * self.activation(gate)
        return self.dense_2(x)

    def get_config(self):
        config = super().get_config()
        config.update(
            {
                "hidden_dim": self.hidden_dim,
                "feedforward_expansion_factor": self.feedforward_expansion_factor,  # noqa: E501
                "dtype": self.dtype,
            }
        )
        return config


@keras.saving.register_keras_serializable(package="keras_hub")
class MoonshineLinearGeLU(keras.layers.Layer):
    """
    Moonshine Linear GeLU feedforward layer.

    Implements a feedforward block that applies a linear projection, followed by
    a GeLU activation, and a second linear projection back to the original
    hidden dimension. This layer serves as an alternative to SwiGLU in the
    Moonshine architecture.

    Args:
        hidden_dim: int, The dimensionality of the input and output
        representations. Controls the width of the network.
        feedforward_expansion_factor: int, The factor by which the hidden
        dimension is expanded in the intermediate dense layer. Controls the
        capacity of the feedforward network.
        Defaults to 4.
        dtype: string or `keras.mixed_precision.DTypePolicy`, optional, The
        dtype to use for model computations and weights. Defaults to None.
        **kwargs: Additional keyword arguments passed to the parent class.

    Returns:
        A tensor with the same last dimension as the input (hidden_dim) after
        applying the LinearGeLU feedforward transformation.
    """

    def __init__(
        self,
        hidden_dim,
        feedforward_expansion_factor=4,
        dtype=None,
        **kwargs,
    ):
        super().__init__(dtype=dtype, **kwargs)
        self.hidden_dim = hidden_dim
        self.feedforward_expansion_factor = feedforward_expansion_factor
        # Taken from pretrained weights.
        # First dense layer: output dimension is hidden_dim *
        # feedforward_expansion_factor.
        self.dense_1 = keras.layers.Dense(
            hidden_dim * feedforward_expansion_factor,
            use_bias=True,
            name="dense_1",
            dtype=self.dtype,
        )
        # Activation layer using "gelu".
        self.activation = keras.layers.Activation(
            "gelu",
            name="activation",
            dtype=self.dtype,
        )
        # Second dense layer: output dimension is hidden_dim.
        self.dense_2 = keras.layers.Dense(
            hidden_dim,
            use_bias=True,
            name="dense_2",
            dtype=self.dtype,
        )

    def build(self, input_shape):
        super().build(input_shape)
        # Build the first dense layer with the given input shape.
        self.dense_1.build(input_shape)
        # The output of dense_1 will have its last dimension = hidden_dim *
        # feedforward_expansion_factor.
        # Use that output shape to build the second dense layer.
        dense1_output_shape = self.dense_1.compute_output_shape(input_shape)
        self.dense_2.build(dense1_output_shape)

    def call(self, inputs):
        x = self.dense_1(inputs)
        x = self.activation(x)
        return self.dense_2(x)

    def get_config(self):
        config = super().get_config()
        config.update(
            {
                "hidden_dim": self.hidden_dim,
                "feedforward_expansion_factor": self.feedforward_expansion_factor,  # noqa: E501
                "dtype": self.dtype,
            }
        )
        return config


@keras.saving.register_keras_serializable(package="keras_hub")
class MoonshineReversibleEmbedding(keras.layers.Layer):
    """
    Moonshine reversible embedding.

    An embedding layer that maps discrete token indices to continuous embedding
    vectors and supports reversible operations to project hidden representations
    back into vocabulary logits. This reversible functionality enables the layer
    to serve both as an encoder for input tokens and as a decoder output
    projection in transformer models.

    Args:
        vocabulary_size: int, The size of the vocabulary. Determines the number
        of unique tokens that can be embedded.
        hidden_dim: int, The dimensionality of the embedding vectors. Controls
        the richness of the token representations.
        embeddings_initializer: str or callable, Initializer for the embedding
        weights. Determines how embedding values are initialized. Defaults to
        "uniform".
        embeddings_regularizer: str or callable, Regularizer function applied to
        the embedding weights. Controls overfitting of the embeddings. Defaults
        to None.
        embeddings_constraint: str or callable, Constraint function applied to
        the embedding weights. Enforces constraints on the embedding values.
        Defaults to None.
        dtype: string or `keras.mixed_precision.DTypePolicy`, optional, The
        dtype to use for model computations and weights. Defaults to None.
        **kwargs: Additional keyword arguments passed to the parent class.

    Returns:
        When reverse=False: A tensor of embedded token representations.
        When reverse=True: A tensor of logits for each token in the vocabulary.
    """

    def __init__(
        self,
        vocabulary_size,
        hidden_dim,
        embeddings_initializer="uniform",
        embeddings_regularizer=None,
        embeddings_constraint=None,
        dtype=None,
        **kwargs,
    ):
        super().__init__(dtype=dtype, **kwargs)
        self.vocabulary_size = vocabulary_size
        self.hidden_dim = hidden_dim
        self.embeddings_initializer = keras.initializers.get(
            embeddings_initializer
        )
        self.embeddings_regularizer = keras.regularizers.get(
            embeddings_regularizer
        )
        self.embeddings_constraint = keras.constraints.get(
            embeddings_constraint
        )

    def build(self, input_shape):
        self.embeddings = self.add_weight(
            name="embeddings",
            shape=[self.vocabulary_size, self.hidden_dim],
            initializer=self.embeddings_initializer,
            regularizer=self.embeddings_regularizer,
            constraint=self.embeddings_constraint,
            dtype=self.dtype,
            trainable=True,
        )
        super().build(input_shape)

    def call(self, inputs, reverse=False):
        if reverse:
            kernel = keras.ops.transpose(
                keras.ops.convert_to_tensor(self.embeddings)
            )
            return keras.ops.matmul(inputs, kernel)
        else:
            return keras.ops.take(self.embeddings, inputs, axis=0)

    def compute_output_shape(self, input_shape):
        if not input_shape:
            raise ValueError("Input shape must be non-empty")

        output_shape = list(input_shape)
        if hasattr(self, "_reverse") and self._reverse:
            output_shape[-1] = self.vocabulary_size
        else:
            output_shape.append(self.hidden_dim)
        return tuple(output_shape)

    def get_config(self):
        config = super().get_config()
        config.update(
            {
                "vocabulary_size": self.vocabulary_size,
                "hidden_dim": self.hidden_dim,
                "embeddings_initializer": keras.initializers.serialize(
                    self.embeddings_initializer
                ),
                "embeddings_regularizer": keras.regularizers.serialize(
                    self.embeddings_regularizer
                ),
                "embeddings_constraint": keras.constraints.serialize(
                    self.embeddings_constraint
                ),
                "dtype": self.dtype,
            }
        )
        return config
