-- The final comparable warehouse table: one consistent schema across every
-- platform, safe to compare directly without platform-specific caveats.

with normalized_raw as (

    select *
    from {{ source('normalized', 'marketing_metrics_raw') }}

),

deduplicated as (

    select
        *,
        row_number() over (
            partition by platform, date
            order by _loaded_at desc
        ) as row_num

    from normalized_raw

),

final as (

    select
        platform,
        date,
        spend_usd,
        native_currency,
        impressions,
        conversions,
        safe_divide(spend_usd, nullif(conversions, 0)) as cost_per_conversion,
        safe_divide(spend_usd, nullif(impressions, 0)) * 1000 as cpm

    from deduplicated
    where row_num = 1

)

select * from final
