-- 014: Mortgage market view — joins BoF housing-loan metrics with the
-- 12mo Euribor for a single composite "what does borrowing actually cost
-- right now in Finland" lens. Idempotent (CREATE OR REPLACE).

CREATE OR REPLACE VIEW property.v_mortgage_market AS
WITH bof_pivot AS (
    SELECT
        period,
        MAX(value) FILTER (WHERE metric_code = 'avg_rate_new_loans')   AS avg_rate_new_loans_pct,
        MAX(value) FILTER (WHERE metric_code = 'avg_rate_outstanding') AS avg_rate_outstanding_pct,
        MAX(value) FILTER (WHERE metric_code = 'new_loans_volume_meur') AS new_loans_volume_meur,
        MAX(value) FILTER (WHERE metric_code = 'stock_meur')           AS housing_loan_stock_meur
    FROM property.bof_housing_loan_metric
    GROUP BY period
),
euribor_monthly AS (
    SELECT
        date_trunc('month', observation_date)::date AS period,
        AVG(value_pct) AS euribor_12m_pct
    FROM property.interest_rate
    WHERE rate_type = 'euribor_12m'
    GROUP BY date_trunc('month', observation_date)
)
SELECT
    b.period,
    b.avg_rate_new_loans_pct,
    b.avg_rate_outstanding_pct,
    e.euribor_12m_pct,
    -- Implied bank margin: new-loan rate above the 12mo Euribor benchmark
    CASE
        WHEN b.avg_rate_new_loans_pct IS NOT NULL AND e.euribor_12m_pct IS NOT NULL
        THEN ROUND((b.avg_rate_new_loans_pct - e.euribor_12m_pct)::numeric, 3)
        ELSE NULL
    END AS implied_margin_pp,
    b.new_loans_volume_meur,
    b.housing_loan_stock_meur
FROM bof_pivot b
LEFT JOIN euribor_monthly e USING (period)
ORDER BY b.period DESC;

COMMENT ON VIEW property.v_mortgage_market IS
    'Monthly Finnish mortgage-market summary: BoF avg new/outstanding rates, ECB 12mo Euribor benchmark, implied bank margin (new_rate - euribor_12m), new business volume and outstanding stock.';
