-- Diagnósticos de solo lectura para TERCEROS, DATOSADICIONALES, PRODUCTOS y TIPSTRAN (ejecutar en el iSeries).
-- Sustituir @@PROD_A@@ / @@PROD_B@@ por las listas de config.yaml en los de PRODUCTOS.
-- Los largos máximos se comparan con el destino: NUMIDE 20, códigos 30, TIPO/SEXO/PEP/ESTADO... 1, DIRECCION 200.

-- ===== TERCEROS (psa065, estado 'A')
-- T1) Largo máximo por columna sin recortar (si superan el destino, SQL Server dará error de truncamiento)
select max(length(psa065td)) l_tipide, max(length(psa065do)) l_numide, max(length(trim(psa065nm))) l_nombres,
       max(length(psa065ci)) l_sisgeo, max(length(psa065ti)) l_tipo, max(length(psa065ri)) l_tipcal,
       max(length(psa065di)) l_direccion, max(length(psa065ea)) l_exento, max(length(psa065es)) l_estado,
       max(length(psa065an)) l_autoriza, max(length(trim(psa065pe))) l_pep, max(length(trim(psa065sx))) l_sexo,
       max(length(psa065nl)) l_empresa, max(length(psa065dl)) l_dir_empresa, max(length(psa065pa)) l_lugar_exp
from gnbweb_db.psa065 where psa065es = 'A';
-- T2) NULL en columnas NOT NULL del destino (TIPIDE, NUMIDE, NOMBRES, SISGEO_CODIGO, TIPO, EXENTOANALISIS, ESTADO)
select sum(case when psa065td is null then 1 else 0 end) n_tipide, sum(case when psa065do is null then 1 else 0 end) n_numide,
       sum(case when psa065nm is null then 1 else 0 end) n_nombres, sum(case when psa065ci is null then 1 else 0 end) n_sisgeo,
       sum(case when psa065ti is null then 1 else 0 end) n_tipo, sum(case when psa065ea is null then 1 else 0 end) n_exento
from gnbweb_db.psa065 where psa065es = 'A';
-- T3) Documentos con espacios al inicio (PRODUCTOS sí los recorta con TRIM; TERCEROS y TRANSACCIONES no) y claves repetidas
select count(*) con_espacio_inicial from gnbweb_db.psa065 where psa065es = 'A' and psa065do <> ltrim(psa065do);
select count(*) tercero_repetido from (select psa065td, psa065do from gnbweb_db.psa065 where psa065es = 'A'
                                        group by psa065td, psa065do having count(*) > 1) d;

-- ===== DATOSADICIONALES (psa065 + psa002)
-- D1) Un mismo (tipo, documento) con varios psa065pa distintos puede salir en AMBAS ramas ('1' y '0'): clave repetida
select count(*) con_ambos_valores from (
  select p.psa065td, p.psa065do from gnbweb_db.psa065 p where p.psa065es = 'A'
   group by p.psa065td, p.psa065do
  having count(distinct case when exists (select 1 from gnbweb_db.psa002 where psa002pa = p.psa065pa and psa002to = p.psa065td
                                          and psa002do = p.psa065do) then '1' else '0' end) > 1) d;

-- ===== PRODUCTOS (psa066)
-- P1) Filas por rama y sin producto (psa066mo NULL: el script actual las descarta)
select sum(case when psa066mo in (@@PROD_A@@) then 1 else 0 end) rama_a, sum(case when psa066mo in (@@PROD_B@@) then 1 else 0 end) rama_b,
       sum(case when psa066mo not in (@@PROD_A@@,@@PROD_B@@) then 1 else 0 end) rama_resto,
       sum(case when psa066mo is null then 1 else 0 end) sin_producto, count(*) total from gnbweb_db.psa066;
-- P2) Fechas de apertura que no son AAAAMMDD válidas (FECHAAPERTURA es NOT NULL: una fecha inválida aborta la carga)
select count(*) fecha_invalida from gnbweb_db.psa066
 where psa066fa <> '00000000' and (length(trim(psa066fa)) <> 8 or psa066fa not between '17530101' and '99991231'
       or substr(psa066fa,5,2) not between '01' and '12' or substr(psa066fa,7,2) not between '01' and '31');
-- P3) NULL en columnas NOT NULL (NUMERO, SUCURSAL, CUPO) y largos (NUMERO <= 100, SUCURSAL <= 30)
select sum(case when psa066cu is null then 1 else 0 end) n_cupo, sum(case when psa066su is null then 1 else 0 end) n_sucursal,
       sum(case when psa066op is null or psa066so is null or psa066ct is null then 1 else 0 end) n_numero,
       max(length(psa066op||'-'||psa066so)) l_numero_a, max(length(psa066ct||'-'||psa066so)) l_numero_resto,
       max(length(psa066ct||'-'||psa066op)) l_numero_b from gnbweb_db.psa066;

-- ===== TIPSTRAN (psa068, estado 'A')
-- S1) Códigos repetidos y valores más largos que el destino (NATURALEZA/MANEJAEFECTIVO/INCLUIR/INCLUIRMOV/ANALISISPERFIL/ALERTAMANDATORIA = 1)
select count(*) total, count(distinct psa068co) codigos, max(length(psa068na)) l_nat, max(length(psa068ef)) l_efe,
       max(length(psa068in)) l_inc, max(length(psa068im)) l_incmov, max(length(psa068ap)) l_ap, max(length(psa068am)) l_am,
       max(length(psa068ds)) l_nombre, max(length(psa068co)) l_codigo
from gnbweb_db.psa068 where psa068st = 'A';
