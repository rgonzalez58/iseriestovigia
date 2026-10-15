-- PROPUESTA (no registrada en config.yaml; el script vigente es datosadicionales.sql).
-- UNION -> UNION ALL: las dos ramas son complementarias (EXISTS / NOT EXISTS) y devuelven VALORDATO distinto
-- ('1' / '0'), y cada rama ya agrupa por (tipo, número de documento), así que el UNION nunca elimina filas.
-- Mismo resultado sin la ordenación/comparación de duplicados.
select p.psa065td TERCERO_TIPIDE_CODIGO,
	   p.psa065do	TERCERO_NUMIDE,
	   'LISTAS_RESTRICTIVAS' TIPODATOADICIONAL_CODIGO,
	   '1' VALORDATO
  from gnbweb_db.psa065 p
 where exists (select 1 from gnbweb_db.psa002 WHERE psa002pa = p.psa065pa and psa002to = p.psa065td and psa002do = p.psa065do)
 AND p.PSA065ES = 'A'
 GROUP by psa065td, psa065do
 UNION ALL
 select p.psa065td TERCERO_TIPIDE_CODIGO,
	   p.psa065do	TERCERO_NUMIDE,
	   'LISTAS_RESTRICTIVAS' TIPODATOADICIONAL_CODIGO,
	   '0' VALORDATO
  from gnbweb_db.psa065 p
 where NOT exists (select 1 from gnbweb_db.psa002 WHERE psa002pa = p.psa065pa and psa002to = p.psa065td and psa002do = p.psa065do)
 AND p.PSA065ES = 'A'
 GROUP by psa065td, psa065do
