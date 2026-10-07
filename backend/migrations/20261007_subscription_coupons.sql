CREATE TABLE IF NOT EXISTS subscription_coupons (
  id uuid PRIMARY KEY, code text UNIQUE NOT NULL, active boolean NOT NULL DEFAULT true,
  price_paise integer NOT NULL, duration_months integer NOT NULL, plan_id text NOT NULL,
  per_candidate_usage integer NOT NULL DEFAULT 1, starts_at timestamptz, ends_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS subscription_coupon_redemptions (
  id uuid PRIMARY KEY, coupon_id uuid NOT NULL REFERENCES subscription_coupons(id), candidate_id uuid NOT NULL,
  payment_attempt_id uuid UNIQUE, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(coupon_id, candidate_id)
);
