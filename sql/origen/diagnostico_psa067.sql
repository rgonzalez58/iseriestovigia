-- Diagnósticos de solo lectura sobre gnbweb_db.psa067 (ejecutar en el iSeries; no modifican datos).
-- Sustituir @@PROD_A@@ y @@PROD_B@@ por las listas de config.yaml si se ejecutan a mano.

-- 1) Filas por rama y filas sin producto (psa067mo NULL: el script actual las descarta).
select sum(case when psa067mo in (@@PROD_A@@) then 1 else 0 end) rama_a,
       sum(case when psa067mo in (@@PROD_B@@) then 1 else 0 end) rama_b,
       sum(case when psa067mo not in (@@PROD_A@@,@@PROD_B@@) then 1 else 0 end) rama_resto,
       sum(case when psa067mo is null then 1 else 0 end) sin_producto,
       count(*) total
from gnbweb_db.psa067;

-- 2) Largo máximo de las columnas convertidas (PRODUCTO_NUMERO <= 20, INTERMEDIARIO_CODIGO <= 30)
--    y NULL en columnas NOT NULL del destino (producen error de carga).
select max(length(psa067op||'-'||trim(psa067so))) len_prod_a,
       max(length(psa067nc||'-'||trim(psa067so))) len_prod_resto,
       max(length(psa067nc||'-'||trim(psa067op))) len_prod_b,
       max(length('CAN-'||trim(psa067ca))) len_interm,
       sum(case when psa067op is null or psa067so is null or psa067nc is null then 1 else 0 end) nulos_numero,
       sum(case when psa067mc is null or psa067tx is null then 1 else 0 end) nulos_tiptran,
       sum(case when psa067fc is null then 1 else 0 end) nulos_fecha,
       sum(case when psa067co is null or psa067va is null then 1 else 0 end) nulos_consec_valor
from gnbweb_db.psa067;

-- 3) ¿Hay filas totalmente duplicadas? (si la diferencia es 0, UNION y UNION ALL dan lo mismo)
select count(*) filas, count(distinct psa067td||'|'||psa067do||'|'||psa067mo||'|'||psa067op||'|'||psa067so||'|'||
       psa067nc||'|'||psa067mc||'|'||psa067tx||'|'||psa067fc||'|'||psa067co||'|'||psa067ci||'|'||psa067su||'|'||
       psa067va||'|'||psa067me||'|'||psa067mn||'|'||psa067ca||'|'||psa067pc||'|'||psa067rl||'|'||psa067or||'|'||
       psa067os) distintas
from gnbweb_db.psa067;   -- ojo: la concatenación devuelve NULL si alguna columna es NULL; usar COALESCE si hace falta

-- 4) Rango de fechas y filas por mes (para dimensionar una carga incremental por fecha).
select substr(char(psa067fc),1,6) mes, count(*) filas from gnbweb_db.psa067 group by substr(char(psa067fc),1,6) order by 1;
