-- PROPUESTA (no registrada en config.yaml; el script vigente es transacciones.sql, idéntico a Origen_PSA003.txt).
-- Una sola lectura de psa067 en lugar de tres. Mismas columnas, mismos alias y mismos valores.
-- Variante A (esta): DISTINCT conserva la semántica del UNION original (elimina filas exactamente iguales).
-- Variante B: quitar la palabra DISTINCT (equivale a UNION ALL); solo si el diagnóstico 3 de
--             diagnostico_psa067.sql confirma que no hay filas duplicadas en el origen.
select distinct psa067td TERCERO_TIPIDE_CODIGO,
	psa067do TERCERO_NUMIDE,
	case psa067mn when '6900' then 'PRO-'||trim(psa067mo)||'-GS' else 'PRO-'||trim(psa067mo)||'-ME' end as PRODUCTO_CODIGO,
	case when psa067mo in (@@PROD_B@@) then psa067nc||'-'||trim(psa067op)
	     when psa067mo in (@@PROD_A@@) then psa067op||'-'||trim(psa067so)
	     else psa067nc||'-'||trim(psa067so) end PRODUCTO_NUMERO,
	trim(psa067mc)||'-'||trim(psa067tx) TIPTRAN_CODIGO,
	psa067fc FECHA,
	psa067co CONSECUTIVO,
	psa067ci SISGEO_CODIGO,
	psa067su SUCURSAL_CODIGO,
	psa067va VALOR,
	psa067me VALORMONEDAEXTRANJERA,
	psa067mn TIPMON_CODIGO,
	'CAN-'||trim(psa067ca) INTERMEDIARIO_CODIGO,
	psa067pc CODIGOPAISREMITE,
	'Relacion: '||psa067rl|| ' - Ordinal: '||trim(psa067or)||' - SubOrd: '||trim(psa067os) as REFERENCIA1
from gnbweb_db.psa067
where psa067mo is not null      -- el original excluye NULL en las tres ramas (IN / NOT IN con NULL no devuelve filas)
