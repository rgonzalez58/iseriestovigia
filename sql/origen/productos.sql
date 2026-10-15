select psa066td  TERCERO_TIPIDE_CODIGO,
	   trim(psa066do) TERCERO_NUMIDE,
	   case psa066mn WHEN '6900' THEN 'PRO-'||TRIM(psa066mo)||'-GS' ELSE 'PRO-'||TRIM(psa066mo)||'-ME' END as  PRODUCTO_CODIGO,
	   psa066op||'-'||psa066so  NUMERO,
	   psa066su SUCURSAL_CODIGO,
	   case psa066fa when '00000000' then '17530101' else psa066fa end FECHAAPERTURA,
	   psa066cu CUPO,
	   psa066sd SALDO,
	   psa066st	VIGENCIA_CODIGO,
	   psa066ti	TITULARIDAD_CODIGO
  from gnbweb_db.psa066 p
 where p.psa066mo in (@@PROD_A@@)
union 
select psa066td  TERCERO_TIPIDE_CODIGO,
	   trim(psa066do) TERCERO_NUMIDE,
	   case psa066mn WHEN '6900' THEN 'PRO-'||TRIM(psa066mo)||'-GS' ELSE 'PRO-'||TRIM(psa066mo)||'-ME' END as  PRODUCTO_CODIGO,
	   psa066ct||'-'||psa066so  NUMERO,
	   psa066su SUCURSAL_CODIGO,
	   case psa066fa when '00000000' then '17530101' else psa066fa end FECHAAPERTURA,
	   psa066cu CUPO,
	   psa066sd SALDO,
	   psa066st	VIGENCIA_CODIGO,
	   psa066ti	TITULARIDAD_CODIGO
  from gnbweb_db.psa066 p
 where p.psa066mo not in (@@PROD_A@@,@@PROD_B@@)
 UNION
 select psa066td  TERCERO_TIPIDE_CODIGO,
	   trim(psa066do) TERCERO_NUMIDE,
	   case psa066mn WHEN '6900' THEN 'PRO-'||TRIM(psa066mo)||'-GS' ELSE 'PRO-'||TRIM(psa066mo)||'-ME' END as  PRODUCTO_CODIGO,
	   psa066ct||'-'||psa066op  NUMERO,
	   psa066su SUCURSAL_CODIGO,
	   case psa066fa when '00000000' then '17530101' else psa066fa end FECHAAPERTURA,
	   psa066cu CUPO,
	   psa066sd SALDO,
	   psa066st	VIGENCIA_CODIGO,
	   psa066ti	TITULARIDAD_CODIGO
  from gnbweb_db.psa066 p
 where p.psa066mo in (@@PROD_B@@)
