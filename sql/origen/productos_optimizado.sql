-- PROPUESTA (no registrada en config.yaml; el script vigente es productos.sql).
-- Una sola lectura de psa066 en lugar de tres. Mismas columnas y valores; DISTINCT conserva la semántica del UNION.
-- Variante B: quitar DISTINCT (UNION ALL) solo si el diagnóstico confirma que no hay filas duplicadas.
select distinct psa066td  TERCERO_TIPIDE_CODIGO,
	   trim(psa066do) TERCERO_NUMIDE,
	   case psa066mn WHEN '6900' THEN 'PRO-'||TRIM(psa066mo)||'-GS' ELSE 'PRO-'||TRIM(psa066mo)||'-ME' END as  PRODUCTO_CODIGO,
	   case when p.psa066mo in (@@PROD_B@@) then psa066ct||'-'||psa066op
	        when p.psa066mo in (@@PROD_A@@) then psa066op||'-'||psa066so
	        else psa066ct||'-'||psa066so end NUMERO,
	   psa066su SUCURSAL_CODIGO,
	   case psa066fa when '00000000' then '17530101' else psa066fa end FECHAAPERTURA,
	   psa066cu CUPO,
	   psa066sd SALDO,
	   psa066st	VIGENCIA_CODIGO,
	   psa066ti	TITULARIDAD_CODIGO
  from gnbweb_db.psa066 p
 where p.psa066mo is not null      -- el original excluye NULL en las tres ramas
