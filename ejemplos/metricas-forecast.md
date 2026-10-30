# Métricas de forecast y precisión del modelo de ventas

Documento de negocio para el agente doc → Ossie. Describe las métricas que
queremos sobre las predicciones del modelo de ventas y sobre su precisión,
para usarlas en preguntas en lenguaje natural y en el dashboard predictivo
de Metabase.

## Contexto

- La tabla `Predictions` guarda cada predicción del modelo: el valor
  `Predicted Sales` es la venta prevista de **una línea de pedido típica**
  (perfil representativo por región y categoría), no el total del mes.
  Contiene predicciones de distintos runs (`Run ID`) y entornos
  (`Environment`); para el forecast vigente hay que filtrar por el entorno
  `prod` y el run más reciente.
- La vista `Forecast Accuracy` compara, para los meses de holdout que el
  modelo no vio al entrenar, las ventas reales (`Actual Sales`) con las
  ventas que predijo para esas mismas líneas (`Predicted Sales`), por mes,
  región y categoría, y su error absoluto mensual (`Abs Error`).

## Métricas de forecast (tabla Predictions)

1. **Ventas predichas**: suma de las ventas predichas. Sirve para comparar
   meses o regiones dentro de un mismo run, no como total mensual real.
2. **Ticket medio predicho**: media de las ventas predichas por línea de
   pedido. Es el número de predicción que enseñamos en el dashboard.

## Métricas de precisión (vista Forecast Accuracy)

3. **Ventas reales del backtest**: suma de las ventas reales evaluadas.
4. **Ventas predichas del backtest**: suma de lo que el modelo predijo para
   esas mismas líneas. Junto con la anterior da el gráfico previsto vs real.
5. **Error absoluto**: suma del error absoluto mensual.
6. **Error porcentual ponderado (WAPE)**: error absoluto dividido entre las
   ventas reales. Es el indicador principal de precisión y la base del
   drift monitoring: si sube entre runs, el modelo se está degradando.
7. **Sesgo del forecast**: (ventas predichas − ventas reales) / ventas
   reales. Positivo = el modelo sobreestima, negativo = subestima.

Todas las métricas de precisión mezclan runs si no se filtra por `Run ID`;
para evaluar un modelo concreto hay que filtrar por su run.
