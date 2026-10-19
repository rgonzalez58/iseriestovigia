select psa067td TERCERO_TIPIDE_CODIGO,
	psa067do TERCERO_NUMIDE,
	case psa067mn when '6900' then 'PRO-'||trim(psa067mo)||'-GS' else 'PRO-'||trim(psa067mo)||'-ME' end as PRODUCTO_CODIGO,
	psa067op||'-'||trim(psa067so)  PRODUCTO_NUMERO,
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
where psa067mo in (@@PROD_A@@) @@FILTRO_FECHA@@
union 
select psa067td TERCERO_TIPIDE_CODIGO,
	psa067do TERCERO_NUMIDE,
	case psa067mn when '6900' then 'PRO-'||trim(psa067mo)||'-GS' else 'PRO-'||trim(psa067mo)||'-ME' END AS PRODUCTO_CODIGO,
	psa067nc||'-'||trim(psa067so)  PRODUCTO_NUMERO,
	trim(psa067mc)||'-'||trim(psa067tx) TIPTRAN_CODIGO,
	psa067fc FECHA,
	psa067co CONSECUTIVO,
	psa067ci SISGEO_CODIGO,
	psa067su SUCURSAL_CODIGO,
	psa067va VALOR,
	psa067me VALORMONEDAEXTRANJERA,
	psa067mn TIPMON_CODIGO,
	'CAN-'||TRIM(psa067ca) INTERMEDIARIO_CODIGO,
	psa067pc CODIGOPAISREMITE,
	'Relacion: '||psa067rl|| ' - Ordinal: '||TRIM(psa067or)||' - SubOrd: '||trim(psa067os) as REFERENCIA1
from gnbweb_db.psa067
where psa067mo not in (@@PROD_A@@,@@PROD_B@@) @@FILTRO_FECHA@@
union
select psa067td TERCERO_TIPIDE_CODIGO,
	psa067do TERCERO_NUMIDE,
	case psa067mn when '6900' then 'PRO-'||trim(psa067mo)||'-GS' else 'PRO-'||trim(psa067mo)||'-ME' end as PRODUCTO_CODIGO,
	psa067nc||'-'||trim(psa067op)  PRODUCTO_NUMERO,
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
from gnbweb_db.psa067 where psa067mo in (@@PROD_B@@) @@FILTRO_FECHA@@
