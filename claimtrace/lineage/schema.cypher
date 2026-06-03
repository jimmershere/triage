CREATE CONSTRAINT claim_claim_id IF NOT EXISTS FOR (n:Claim) REQUIRE n.claim_id IS UNIQUE;
CREATE CONSTRAINT bundle_bundle_id IF NOT EXISTS FOR (n:Bundle) REQUIRE n.bundle_id IS UNIQUE;
CREATE CONSTRAINT batch_batch_root_hash IF NOT EXISTS FOR (n:Batch) REQUIRE n.batch_root_hash IS UNIQUE;
CREATE CONSTRAINT payment_payment_id IF NOT EXISTS FOR (n:Payment) REQUIRE n.payment_id IS UNIQUE;
CREATE CONSTRAINT x835_trn IF NOT EXISTS FOR (n:X835) REQUIRE n.trn IS UNIQUE;
CREATE CONSTRAINT x277_ref IF NOT EXISTS FOR (n:X277) REQUIRE n.ref IS UNIQUE;
CREATE CONSTRAINT adjustment_adj_id IF NOT EXISTS FOR (n:AdjustmentEvent) REQUIRE n.adj_id IS UNIQUE;

CREATE INDEX claim_lineage_claim_id IF NOT EXISTS FOR (n:Claim) ON (n.claim_id);
CREATE INDEX bundle_lineage_bundle_id IF NOT EXISTS FOR (n:Bundle) ON (n.bundle_id);
CREATE INDEX payment_lineage_payment_id IF NOT EXISTS FOR (n:Payment) ON (n.payment_id);
