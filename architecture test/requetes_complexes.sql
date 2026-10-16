-- Requêtes complexes FICTIVES pour tester l'analyseur SQL (sqlglot) :
-- CTE, sous-requêtes, UNION ALL, CASE, fonctions de fenêtre, INSERT OVERWRITE ... PARTITION,
-- USING, IN (SELECT ...). Résultat attendu : verite_terrain_sql.json.

-- 1. Score de risque : deux CTE, jointures, agrégats, CASE, HAVING, partition Hive
WITH prets_actifs AS (
  SELECT c.code_tiers, c.num_contrat, c.montant_initial
  FROM bv_pret.contrats c
  WHERE c.statut = 'ACTIF'
),
retards AS (
  SELECT e.num_contrat, count(*) AS nb_retards, max(e.jours_retard) AS retard_max
  FROM bv_pret.echeances e
  WHERE e.jours_retard > 0
  GROUP BY e.num_contrat
)
INSERT OVERWRITE TABLE dm_risque.score_client PARTITION (dt = '2026-10-01')
SELECT t.code_tiers,
       t.nom_client,
       sum(p.montant_initial) AS encours_total,
       coalesce(max(r.retard_max), 0) AS retard_max,
       CASE WHEN max(r.retard_max) > 90 THEN 'DOUTEUX'
            WHEN max(r.retard_max) > 30 THEN 'SURVEILLE'
            ELSE 'SAIN' END AS classe_risque
FROM bv_client.tiers t
JOIN prets_actifs p ON p.code_tiers = t.code_tiers
LEFT JOIN retards r ON r.num_contrat = p.num_contrat
GROUP BY t.code_tiers, t.nom_client
HAVING sum(p.montant_initial) > 0;

-- 2. Vue qui réunit deux canaux (UNION ALL, renommage dans la 2e branche)
CREATE OR REPLACE VIEW bv_canal.demandes_toutes AS
SELECT w.id_demande, w.code_tiers, w.montant, 'WEB' AS canal
FROM lac_web.demandes_web w
UNION ALL
SELECT a.id_demande, a.code_tiers, a.montant_demande AS montant, 'AGENCE' AS canal
FROM lac_agence.demandes_agence a;

-- 3. Dernier contact par client : sous-requête dans le FROM + fonction de fenêtre (CTAS)
CREATE TABLE dm_client.dernier_contact AS
SELECT x.code_tiers, x.canal, x.date_contact
FROM (
  SELECT ct.code_tiers, ct.canal, ct.date_contact,
         row_number() OVER (PARTITION BY ct.code_tiers ORDER BY ct.date_contact DESC) AS rang
  FROM lac_crm.contacts ct
) x
WHERE x.rang = 1;

-- 4. Colonnes cibles déclarées + jointure USING
INSERT INTO TABLE dm_reseau.agences_region (code_agence, libelle_agence, nom_region)
SELECT ag.code_agence, ag.libelle_agence, rg.nom_region
FROM bv_ref.agences ag
JOIN bv_ref.regions rg USING (code_region);

-- 5. Requête d'analyse avec IN (SELECT ...)
SELECT o.num_compte, sum(o.montant) AS total
FROM bv_cpt.operations o
WHERE o.num_compte IN (SELECT c.num_compte FROM bv_cpt.comptes c WHERE c.type_compte = 'COURANT')
GROUP BY o.num_compte;
