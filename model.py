from keras import backend as K
from keras.engine import Model
from keras.layers import Input, Dense, Conv2D, Embedding, Conv1D, TimeDistributed, GlobalMaxPooling1D, Concatenate, \
    Reshape
from keras.models import Sequential

from feature_extractors.densenet import DenseNet
from layers.decaying_dropout import DecayingDropout
from layers.encoding import Encoding
from layers.interaction import Interaction


class DIIN(Model):
    def __init__(self,
                 p=None, h=None,
                 include_word_vectors=True, word_embedding_weights=None, train_word_embeddings=True,
                 include_chars=True, chars_per_word=3, char_conv_kernel_size=5,
                 include_syntactical_features=False, syntactical_feature_size=50,
                 include_exact_match=True,
                 dropout_initial_keep_rate=1., dropout_decay_rate=0.977, dropout_decay_interval=10000,
                 first_scale_down_ratio=0.3, transition_scale_down_ratio=0.5, growth_rate=20,
                 layers_per_dense_block=8, nb_dense_blocks=3, nb_labels=1,
                 inputs=None, outputs=None, name='DIIN'):
        """
        :ref https://openreview.net/forum?id=r1dHXnH6-&noteId=r1dHXnH6-

        :param p: sequence length of premise
        :param h: sequence length of hypothesis
        :param include_word_vectors: whether or not to include word vectors in the model
        :param word_embedding_weights: matrix of weights for word embeddings (GloVe pre-trained vectors)
        :param train_word_embeddings: whether or not to modify word embeddings while training
        :param include_chars: whether or not to include character embeddings in the model
        :param chars_per_word: how many chars are there per one word (a fixed number)
        :param char_embedding_size: input size of the character-embedding layer
        :param char_conv_filters: number of conv-filters applied on character embedding
        :param char_conv_kernel_size: size of the kernel applied on character embeddings
        :param include_syntactical_features: whether or not to include syntactical features (POS tags) in the model
        :param syntactical_feature_size: size of the syntactical feature vector for each word
        :param include_exact_match: whether or not to include exact match features in the model
        :param dropout_initial_keep_rate: initial state of dropout
        :param dropout_decay_rate: how much to change dropout at each interval
        :param dropout_decay_interval: how much time to wait for the next update
        :param first_scale_down_ratio: first scale down ratio in densenet
        :param transition_scale_down_ratio: transition scale down ratio in densenet
        :param growth_rate: growing rate in densenet
        :param layers_per_dense_block: number of layers in one dense-block
        :param nb_dense_blocks: number of dense blocks in densenet
        :param nb_labels: number of labels (3 labels by default: entailment, contradiction, neutral)
        """

        if inputs or outputs:
            super(DIIN, self).__init__(inputs=inputs, outputs=outputs, name=name)
            return

        if include_word_vectors:
            assert word_embedding_weights is not None
        inputs = []
        premise_embeddings = []
        hypothesis_embeddings = []

        '''Embedding layer'''
        # 1. Word embedding input
        if include_word_vectors == 1:
            premise_word_input    = Input(shape=(p,), dtype='int64', name='PremiseWordInput')
            hypothesis_word_input = Input(shape=(h,), dtype='int64', name='HypothesisWordInput')
            inputs.append(premise_word_input)
            inputs.append(hypothesis_word_input)

            word_embedding = Embedding(input_dim=word_embedding_weights.shape[0],
                                       output_dim=word_embedding_weights.shape[1],
                                       weights=[word_embedding_weights],
                                       trainable=train_word_embeddings,
                                       name='WordEmbedding')
            premise_word_embedding    = word_embedding(premise_word_input)
            hypothesis_word_embedding = word_embedding(hypothesis_word_input)

            premise_word_embedding    = DecayingDropout(initial_keep_rate=dropout_initial_keep_rate,
                                                        decay_interval=dropout_decay_interval,
                                                        decay_rate=dropout_decay_rate,
                                                        name='PremiseWordEmbeddingDropout')(premise_word_embedding)
            hypothesis_word_embedding = DecayingDropout(initial_keep_rate=dropout_initial_keep_rate,
                                                        decay_interval=dropout_decay_interval,
                                                        decay_rate=dropout_decay_rate,
                                                        name='HypothesisWordEmbeddingDropout')(hypothesis_word_embedding)
            premise_embeddings.append(premise_word_embedding)
            hypothesis_embeddings.append(hypothesis_word_embedding)

        # 2. Character input
        if include_chars == 1:
            premise_char_input    = Input(shape=(p, chars_per_word,), name='PremiseCharInput')
            hypothesis_char_input = Input(shape=(h, chars_per_word,), name='HypothesisCharInput')
            inputs.append(premise_char_input)
            inputs.append(hypothesis_char_input)

            # Share weights of character-level embedding for premise and hypothesis
            character_embedding_layer = TimeDistributed(Sequential([
                Embedding(input_dim=word_embedding_weights.shape[0],
                                       output_dim=word_embedding_weights.shape[1],
                                       weights=[word_embedding_weights],
                                       trainable=True,
                                       input_length=chars_per_word),
                Conv1D(filters=word_embedding_weights.shape[1], kernel_size=char_conv_kernel_size),
                GlobalMaxPooling1D()
            ]), name='CharEmbedding')
            character_embedding_layer.build(input_shape=(None, None, chars_per_word))
            premise_char_embedding    = character_embedding_layer(premise_char_input)
            hypothesis_char_embedding = character_embedding_layer(hypothesis_char_input)
            premise_embeddings.append(premise_char_embedding)
            hypothesis_embeddings.append(hypothesis_char_embedding)

        # 3. Syntactical features
        if include_syntactical_features:
            premise_syntactical_input    = Input(shape=(p, syntactical_feature_size,), name='PremiseSyntacticalInput')
            hypothesis_syntactical_input = Input(shape=(h, syntactical_feature_size,), name='HypothesisSyntacticalInput')
            inputs.append(premise_syntactical_input)
            inputs.append(hypothesis_syntactical_input)
            premise_embeddings.append(premise_syntactical_input)
            hypothesis_embeddings.append(hypothesis_syntactical_input)

        # 4. One-hot exact match feature
        if include_exact_match == 1:
            premise_exact_match_input    = Input(shape=(p,), name='PremiseExactMatchInput')
            hypothesis_exact_match_input = Input(shape=(h,), name='HypothesisExactMatchInput')
            premise_exact_match    = Reshape(target_shape=(p, 1,))(premise_exact_match_input)
            hypothesis_exact_match = Reshape(target_shape=(h, 1,))(hypothesis_exact_match_input)
            inputs.append(premise_exact_match_input)
            inputs.append(hypothesis_exact_match_input)
            premise_embeddings.append(premise_exact_match)
            hypothesis_embeddings.append(hypothesis_exact_match)

        # Concatenate all features
        premise_embedding    = Concatenate(name='PremiseEmbedding')(premise_embeddings)
        hypothesis_embedding = Concatenate(name='HypothesisEmbedding')(hypothesis_embeddings)
        d = K.int_shape(hypothesis_embedding)[-1]

        '''Encoding layer'''
        # Now we have the embedded premise [pxd] along with embedded hypothesis [hxd]
        premise_encoding    = Encoding(name='PremiseEncoding')(premise_embedding)
        hypothesis_encoding = Encoding(name='HypothesisEncoding')(hypothesis_embedding)

        '''Interaction layer'''
        interaction = Interaction(name='Interaction')([premise_encoding, hypothesis_encoding])

        '''Feature Extraction layer'''
        feature_extractor_input = Conv2D(filters=int(d * first_scale_down_ratio),
                                         kernel_size=1,
                                         activation=None,
                                         name='FirstScaleDown')(interaction)
        feature_extractor = DenseNet(include_top=False,
                                     input_tensor=Input(shape=K.int_shape(feature_extractor_input)[1:]),
                                     nb_dense_block=nb_dense_blocks,
                                     nb_layers_per_block=layers_per_dense_block,
                                     compression=transition_scale_down_ratio,
                                     growth_rate=growth_rate)(feature_extractor_input)

        '''Output layer'''
        features = DecayingDropout(initial_keep_rate=dropout_initial_keep_rate,
                                   decay_interval=dropout_decay_interval,
                                   decay_rate=dropout_decay_rate,
                                   name='Features')(feature_extractor)
        out = Dense(units=nb_labels, activation='softmax', name='Output')(features)
        super(DIIN, self).__init__(inputs=inputs, outputs=out, name=name)
