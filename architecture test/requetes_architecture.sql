-- =====================================================================================
-- Architecture de test FICTIVE — la même que les fichiers Excel, décrite en SQL (Hive).
-- Importable directement dans Lineage Explorer (bouton « Importer » ou
--   python -m engine build --input "architecture test/requetes_architecture.sql").
-- L'outil en déduit : tables, colonnes, types, clés, flux colonne à colonne et jointures.
-- =====================================================================================

-- ------------------------------------------------------------------ LAC (données brutes)
CREATE TABLE IF NOT EXISTS lac_crm.clients_raw (
  code_tiers      STRING COMMENT 'Identifiant tiers CRM',
  nom_client      STRING COMMENT 'Nom ou raison sociale',
  segment         STRING COMMENT 'Segment CRM (PART, PRO, ENT)',
  code_agence     STRING COMMENT 'Agence de rattachement',
  date_creation   DATE   COMMENT 'Date d''entrée en relation',
  date_chargement TIMESTAMP,
  PRIMARY KEY (code_tiers)
) COMMENT 'Clients bruts issus du CRM' STORED AS PARQUET;

CREATE TABLE IF NOT EXISTS lac_crm.adresses_raw (
  id_adresse      STRING PRIMARY KEY,
  code_tiers      STRING REFERENCES lac_crm.clients_raw (code_tiers),
  ville           STRING,
  code_postal     STRING,
  pays_iso        STRING COMMENT 'Pays (ISO 2)',
  date_chargement TIMESTAMP
) COMMENT 'Adresses postales des tiers';

CREATE TABLE lac_ref.agences_raw (
  code_agence STRING PRIMARY KEY, libelle_agence STRING, code_region STRING, date_chargement TIMESTAMP
) COMMENT 'Référentiel des agences';

CREATE TABLE lac_ref.regions_raw (
  code_region STRING PRIMARY KEY, nom_region STRING, date_chargement TIMESTAMP
) COMMENT 'Référentiel des régions';

CREATE TABLE lac_ref.produits_raw (
  code_produit STRING PRIMARY KEY, libelle_produit STRING, famille_produit STRING,
  taux_base DECIMAL(9,4) COMMENT 'Taux annuel de base', date_chargement TIMESTAMP
) COMMENT 'Catalogue des produits de crédit';

CREATE TABLE lac_ref.canaux_raw (
  code_canal STRING PRIMARY KEY, libelle_canal STRING, date_chargement TIMESTAMP
) COMMENT 'Référentiel des canaux';

CREATE TABLE lac_pret.contrats_raw (
  num_contrat    STRING PRIMARY KEY,
  code_tiers     STRING,
  code_produit   STRING,
  code_agence    STRING,
  montant        DECIMAL(18,2) COMMENT 'Montant emprunté',
  date_signature DATE,
  duree_mois     INT,
  date_chargement TIMESTAMP,
  FOREIGN KEY (code_produit) REFERENCES lac_ref.produits_raw (code_produit)
) COMMENT 'Contrats de prêt bruts';

CREATE TABLE lac_pret.echeances_raw (
  id_echeance STRING PRIMARY KEY, num_contrat STRING, date_echeance DATE,
  montant_echeance DECIMAL(18,2), statut_paiement STRING COMMENT 'PAYE / IMPAYE', date_chargement TIMESTAMP
) COMMENT 'Échéances de remboursement';

CREATE TABLE lac_cpt.comptes_raw (
  num_compte STRING PRIMARY KEY, code_tiers STRING, code_agence STRING, type_compte STRING,
  date_ouverture DATE, date_chargement TIMESTAMP
) COMMENT 'Comptes bancaires';

CREATE TABLE lac_cpt.operations_raw (
  id_operation STRING PRIMARY KEY, num_compte STRING, date_operation DATE,
  montant_op DECIMAL(18,2) COMMENT 'Montant (positif)', sens STRING COMMENT 'C = crédit, D = débit',
  code_canal STRING, date_chargement TIMESTAMP
) COMMENT 'Opérations sur les comptes';

ALTER TABLE lac_pret.echeances_raw ADD CONSTRAINT fk_ech FOREIGN KEY (num_contrat) REFERENCES lac_pret.contrats_raw (num_contrat);

-- ------------------------------------------------------------------ BV (tables de base)
CREATE OR REPLACE VIEW bv_client.tiers AS
SELECT upper(trim(c.code_tiers)) AS code_tiers_ref,
       c.nom_client,
       c.segment AS categorie_client,
       c.code_agence,
       a.ville,
       a.code_postal,
       c.date_creation
FROM lac_crm.clients_raw c
LEFT JOIN lac_crm.adresses_raw a ON a.code_tiers = c.code_tiers;

CREATE OR REPLACE VIEW bv_ref.agences AS
SELECT ag.code_agence, ag.libelle_agence, ag.code_region, r.nom_region
FROM lac_ref.agences_raw ag
JOIN lac_ref.regions_raw r ON r.code_region = ag.code_region;

CREATE OR REPLACE VIEW bv_ref.produits AS
SELECT p.code_produit, p.libelle_produit, p.famille_produit, p.taux_base
FROM lac_ref.produits_raw p;

CREATE OR REPLACE VIEW bv_pret.contrats AS
SELECT k.num_contrat,
       upper(trim(k.code_tiers)) AS code_tiers_ref,
       k.code_produit,
       k.code_agence,
       k.montant AS montant_pret,
       k.date_signature,
       k.duree_mois
FROM lac_pret.contrats_raw k;

CREATE OR REPLACE VIEW bv_pret.echeances AS
SELECT e.id_echeance, e.num_contrat, e.date_echeance, e.montant_echeance, e.statut_paiement
FROM lac_pret.echeances_raw e;

CREATE OR REPLACE VIEW bv_cpt.comptes AS
SELECT cp.num_compte, upper(trim(cp.code_tiers)) AS code_tiers_ref, cp.code_agence, cp.type_compte, cp.date_ouverture
FROM lac_cpt.comptes_raw cp;

CREATE OR REPLACE VIEW bv_cpt.operations AS
SELECT o.id_operation, o.num_compte, o.date_operation,
       o.montant_op AS montant_operation, o.sens, o.code_canal, ca.libelle_canal
FROM lac_cpt.operations_raw o
JOIN lac_ref.canaux_raw ca ON ca.code_canal = o.code_canal;

-- BV construite sur d'autres BV
CREATE OR REPLACE VIEW bv_pret.contrats_enrichis AS
SELECT k.num_contrat, k.code_tiers_ref, k.code_agence, k.code_produit,
       p.libelle_produit, p.famille_produit, p.taux_base,
       k.montant_pret, k.duree_mois, k.date_signature,
       round(k.montant_pret * (p.taux_base / 12) / (1 - power(1 + p.taux_base / 12, -k.duree_mois)), 2) AS mensualite_theorique
FROM bv_pret.contrats k
JOIN bv_ref.produits p ON p.code_produit = k.code_produit;

-- ------------------------------------------------------------------ MART (tables métier)
CREATE TABLE dm_client.vue_360 AS
SELECT t.code_tiers_ref, t.nom_client, t.categorie_client, t.ville, ag.libelle_agence,
       count(DISTINCT cp.num_compte) AS nb_comptes,
       count(DISTINCT k.num_contrat) AS nb_contrats,
       sum(k.montant_pret) AS montant_total_pret
FROM bv_client.tiers t
JOIN bv_ref.agences ag ON ag.code_agence = t.code_agence
LEFT JOIN bv_cpt.comptes cp ON cp.code_tiers_ref = t.code_tiers_ref
LEFT JOIN bv_pret.contrats k ON k.code_tiers_ref = t.code_tiers_ref
GROUP BY t.code_tiers_ref, t.nom_client, t.categorie_client, t.ville, ag.libelle_agence;

CREATE TABLE dm_pret.encours_client (
  code_tiers_ref STRING PRIMARY KEY, nom_client STRING, categorie_client STRING,
  nb_contrats INT, montant_total_pret DECIMAL(18,2), montant_impaye DECIMAL(18,2)
) COMMENT 'Encours de prêt par client';

INSERT OVERWRITE TABLE dm_pret.encours_client
SELECT t.code_tiers_ref, t.nom_client, t.categorie_client,
       count(DISTINCT k.num_contrat),
       sum(k.montant_pret),
       sum(CASE WHEN e.statut_paiement = 'IMPAYE' THEN e.montant_echeance ELSE 0 END)
FROM bv_client.tiers t
JOIN bv_pret.contrats k ON k.code_tiers_ref = t.code_tiers_ref
LEFT JOIN bv_pret.echeances e ON e.num_contrat = k.num_contrat
GROUP BY t.code_tiers_ref, t.nom_client, t.categorie_client;

CREATE TABLE dm_pret.impayes AS
SELECT e.num_contrat, k.code_tiers_ref, k.libelle_produit, k.famille_produit, e.date_echeance,
       e.montant_echeance AS montant_impaye
FROM bv_pret.echeances e
JOIN bv_pret.contrats_enrichis k ON k.num_contrat = e.num_contrat
WHERE e.statut_paiement = 'IMPAYE';

CREATE TABLE dm_reseau.activite_agence AS
SELECT ag.code_agence, ag.libelle_agence, ag.nom_region,
       count(DISTINCT t.code_tiers_ref) AS nb_clients,
       count(DISTINCT k.num_contrat) AS nb_contrats,
       count(DISTINCT cp.num_compte) AS nb_comptes,
       sum(k.montant_pret) AS montant_total_pret
FROM bv_ref.agences ag
LEFT JOIN bv_client.tiers t ON t.code_agence = ag.code_agence
LEFT JOIN bv_pret.contrats k ON k.code_agence = ag.code_agence
LEFT JOIN bv_cpt.comptes cp ON cp.code_agence = ag.code_agence
GROUP BY ag.code_agence, ag.libelle_agence, ag.nom_region;

CREATE TABLE dm_cpt.flux_mensuels AS
SELECT o.num_compte, cp.code_tiers_ref, t.categorie_client,
       substr(o.date_operation, 1, 7) AS mois,
       sum(CASE WHEN o.sens = 'C' THEN o.montant_operation ELSE 0 END) AS total_credits,
       sum(CASE WHEN o.sens = 'D' THEN o.montant_operation ELSE 0 END) AS total_debits,
       count(o.id_operation) AS nb_operations,
       max(o.libelle_canal) AS canal_principal
FROM bv_cpt.operations o
JOIN bv_cpt.comptes cp ON cp.num_compte = o.num_compte
JOIN bv_client.tiers t ON t.code_tiers_ref = cp.code_tiers_ref
GROUP BY o.num_compte, cp.code_tiers_ref, t.categorie_client, substr(o.date_operation, 1, 7);

-- ------------------------------------------------------------------ requête d'analyse (lecture seule)
-- Une simple requête suffit aussi : l'outil en déduit les tables lues et la jointure.
SELECT i.num_contrat, i.montant_impaye, v.nom_client
FROM dm_pret.impayes i
JOIN dm_client.vue_360 v ON v.code_tiers_ref = i.code_tiers_ref;
