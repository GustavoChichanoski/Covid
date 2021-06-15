from src.dataset.generator_seg import SegmentationDataGenerator
from typing import Any, List, Optional, Tuple, Union
from tensorflow.python.keras import Model
from tensorflow.python.keras.callbacks import Callback, EarlyStopping, ModelCheckpoint, ReduceLROnPlateau
from tensorflow.python.keras.engine.base_layer import Layer
from tensorflow.python.keras.engine.input_layer import Input
from tensorflow.python.keras.layers.convolutional import Conv2D, UpSampling2D
from tensorflow.python.keras.layers.core import Dropout
from tensorflow.python.keras.layers.core import Activation
from tensorflow.python.keras.layers.merge import Concatenate
from tensorflow.python.keras.layers.normalization_v2 import BatchNormalization
from tensorflow.python.keras.layers.pooling import MaxPooling2D
from tensorflow.python.keras.regularizers import l1_l2
from tensorflow.python.keras.optimizers import Optimizer
from tensorflow.python.keras.optimizer_v2.adamax import Adamax
from tensorflow.python.keras.metrics import Metric
from tensorflow.python.keras.metrics import BinaryAccuracy
from tensorflow.python.keras import backend as K
from tensorflow.python.keras import regularizers
from src.models.metrics.f1_score import F1score


class Unet(Model):

    def __init__(
        self,
        dim: int = 256,
        activation: str = 'relu',
        n_class: int = 1,
        channels: int = 1,
        depth: int = 5,
        final_activation: str = 'sigmoid',
        filter_root: int = 32,
        name: str = 'UNet',
        **kwargs
    ) -> None:
        super().__init__(name=name,**kwargs)

        # Parametros
        self.dim = dim
        self.n_class = n_class
        self.activation = activation
        self.final_activation = final_activation
        self.channels = channels
        self.filter_root = filter_root
        self.depth = depth
        # Repeateble layers
        self.conv = [None] * self.depth * 4
        self.bn = [None] * self.depth * 4
        self.act = [None] * self.depth * 4
        self.drop = [None] * (self.depth * 4 + 1)
        self.max = [None] * (self.depth - 1)
        self.up = [None] * (self.depth - 1)
        self.cat = [None] * (self.depth - 1)

        self.kernel = (3,3)

        k = 0

        for i in range(depth):
            filters = (2 ** i) * filter_root
            for _ in range(2):
                conv_name = f'conv_{k}'
                self.conv[k] = Conv2D(
                    filters=filters,kernel_size=self.kernel,
                    padding='same', kernel_regularizer=regularizers.l1_l2(l1=1e-5, l2=1e-4),
                    bias_regularizer=regularizers.l2(1e-4),
                    activity_regularizer=regularizers.l2(1e-5),
                    name=conv_name
                )
                k += 1
        for i in range(depth-2,-1,-1):
            filters = (2 ** i) * filter_root
            conv_name = f'conv_{k}'
            for _ in range(2):
                self.conv[k] = Conv2D(
                    filters=filters,kernel_size=self.kernel,
                    padding='same', kernel_regularizer=regularizers.l1_l2(l1=1e-5, l2=1e-4),
                    bias_regularizer=regularizers.l2(1e-4),
                    activity_regularizer=regularizers.l2(1e-5),
                    name=conv_name
                )
                k += 1
        
        self.bn = [BatchNormalization(name=f'bn_{k}') for k in range(len(self.bn))]
        self.act = [Activation(self.activation,name=f'act_{k}') for k in range(len(self.act))]
        self.drop = [Dropout(rate=0.5,name=f'drop_{k}') for k in range(len(self.drop))]

        self.max = [MaxPooling2D((2,2), padding='same', name=f'max_{k}') for k in range(len(self.max))]
        self.up = [UpSampling2D(name=f'up_{k}') for k in range(len(self.up))]
        self.cat = [Concatenate(axis=-1,name=f'cat_{k}') for k in range(len(self.cat))]
        
        # Unique layers
        self.last_conv = Conv2D(
            n_class, (1,1), padding='same', activation=final_activation, name='output'
        )

        # Propriedades da classe
        self._lazy_callbacks = None
        self._lazy_metrics = None

    @property
    def callbacks(self) -> List[Callback]:
        if self._lazy_callbacks is None:
            checkpoint = ModelCheckpoint(
                './.model/best.weights.hdf5', monitor='val_loss',
                verbose=1,save_best_only=True, mode='min',
                save_weights_only=True,
            )
            # Metrica para a redução do valor de LR
            reduce_lr = ReduceLROnPlateau(
                monitor='val_loss', factor=0.5, patience=5,
                verbose=1, mode='min', min_delta=1e-2, cooldown=3,
                min_lr=1e-8
            )
            # Metrica para a parada do treino
            early = EarlyStopping(
                monitor='val_loss', mode='min',
                restore_best_weights=True, patience=40
            )
            # Vetor a ser passado na função fit
            self._lazy_callbacks = [checkpoint, early, reduce_lr]
        return self._lazy_callbacks
    
    @property
    def metrics(self) -> List[Metric]:
        if self._lazy_metrics is None:
            self._lazy_metrics = [BinaryAccuracy(name='accuracy'), F1score()]
        return self._lazy_metrics

    def call(self, inputs):

        store_layers = {}
        first_layer = inputs

        k = 0

        for i in range(self.depth):

            # Cria as duas convoluções da camada
            layer = self.unet_conv(first_layer, k)
            k += 1
            layer = self.unet_conv(layer, k)
            k += 1

            # Verifica se está na ultima camada
            if i < self.depth - 1:
                # Armazena a layer no dicionario
                store_layers[str(i)] = layer
                first_layer = self.max[i](layer)
            else:
                first_layer = layer

        for i in range(self.depth-2,-1,-1):

            connection = store_layers[str(i)]

            layer = self.up[i](first_layer)
            layer = self.cat[i]([layer, connection])

            layer = self.unet_conv(layer, k)
            k += 1
            layer = self.unet_conv(layer, k)
            k += 1

            first_layer = layer

        layer = self.drop[k](layer)
        return self.last_conv(layer)

    def unet_conv(self, layer: Layer, k: int) -> Layer:
        layer = self.conv[k](layer)
        layer = self.bn[k](layer)
        layer = self.act[k](layer)
        layer = self.drop[k](layer)
        return layer

    def fit(
        self, x: SegmentationDataGenerator,
        callbacks: Optional[List[Callback]] = None,
        validation_data: Optional[SegmentationDataGenerator] = None,
        batch_size: Optional[int] = None,
        epochs: int = 100,
        verbose: bool = True,
        shuffle: bool = True,
        **params
    ) -> Any:
        callbacks = self.callbacks if callbacks is None else callbacks
        return super().fit(
            x=x, batch_size=batch_size, epochs=epochs, verbose=verbose,
            callbacks=callbacks, validation_data=validation_data,
            shuffle=shuffle, **params
        )

    def optimizer(self, lr: float = 1e-5) -> Optimizer:
        return Adamax(learning_rate=lr)

    def compile(
        self,
        optimizer: Optional[Optimizer] = None,
        loss: Optional[str] = None,
        metrics: List[Metric] = [None],
        lr: Optional[float] = 1e-5,
        **params
    ) -> None:
        metrics = self.metrics if metrics is None else metrics
        optimizer = self.optimizer(lr) if optimizer is None else optimizer
        loss = dice_coef_loss if loss is None else loss
        return super().compile(
            optimizer=optimizer, loss=loss, metrics=self.metrics, **params
        )

    def save_weights(self, filepath: str, overwrite: bool = True, **params):
        return super().save_weights(filepath, overwrite=overwrite, **params)

    def load_weights(self, filepath: str, **params):
        return super().load_weights(filepath, **params)

def dice_coef(y_true: Any, y_pred: Any) -> Any:
    class_num = 1

    for class_now in range(class_num):
    
        # Converte y_pred e y_true em vetores
            y_true_f = K.flatten(y_true[:,:,:,class_now])
            y_pred_f = K.flatten(y_pred[:,:,:,class_now])

            # Calcula o numero de vezes que
            # y_true(positve) é igual y_pred(positive) (tp)
            intersection = K.sum(y_true_f * y_pred_f)
            # Soma o número de vezes que ambos foram positivos
            union = K.sum(y_true_f) + K.sum(y_pred_f)
            # Smooth - Evita que o denominador fique muito pequeno
            smooth = K.constant(1e-6);
            # Calculo o erro entre eles
            num = (K.constant(2) * intersection + 1)
            den = (union + smooth)
            loss = num / den
            
            if class_now == 0:
                total_loss = loss
            else:
                total_loss = total_loss + loss
        
    total_loss = total_loss / class_num

    return total_loss

def dice_coef_loss(y_true: Any, y_pred: Any) -> Any:
    return 1 - dice_coef(y_true, y_pred)