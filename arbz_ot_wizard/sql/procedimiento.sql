-- Airibuz: función del wizard "Cost OT" (módulo arbz_ot_wizard)
-- Odoo 19: el costo estándar vive en product_product.standard_price (jsonb, clave = id de compañía)
-- y stock_picking.date ya no existe; se usa create_date (equivalente al campo anterior).
CREATE OR REPLACE FUNCTION public.total_subtotal_orden_17(
    date_start DATE,
    date_end DATE
)
RETURNS TABLE(orden CHARACTER VARYING, total NUMERIC)
LANGUAGE plpgsql
AS $function$
BEGIN
    RETURN QUERY
    SELECT
        CASE
            WHEN strpos(stock_picking.origin, '-') > 0
            THEN substring(stock_picking.origin FROM 1 FOR position('-' IN stock_picking.origin) - 1)
            ELSE stock_picking.origin
        END AS orden,
        sum(round(
            ((pp.standard_price ->> stock_picking.company_id::text)::numeric
              * stock_move_line.quantity::numeric), 2
        )) AS total
    FROM
        stock_move_line
    LEFT JOIN stock_picking
        ON stock_move_line.picking_id = stock_picking.id
    JOIN stock_picking_type
        ON stock_picking.picking_type_id = stock_picking_type.id
    JOIN product_product pp
        ON stock_move_line.product_id = pp.id
    JOIN product_template pt
        ON pt.id = pp.product_tmpl_id
    JOIN uom_uom um
        ON pt.uom_id = um.id
    JOIN product_category ct
        ON pt.categ_id = ct.id
    WHERE
        stock_picking.picking_type_id = 261
        AND stock_picking.state = 'done'
        AND stock_picking.create_date::date BETWEEN date_start AND date_end
        AND (pp.standard_price ->> stock_picking.company_id::text) IS NOT NULL
    GROUP BY orden
    ORDER BY orden;
END;
$function$;
