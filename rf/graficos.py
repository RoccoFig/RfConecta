"""Gráficos como SVG generado en el servidor: sin librerías de terceros y con datos reales."""
from markupsafe import Markup, escape


def barras(valores, etiquetas, unidad="visitas", alto=170, ancho=720):
    n = max(len(valores), 1)
    tope = max(max(valores or [0]), 1)
    pie, arriba = 4, 8
    util = alto - pie - arriba
    paso = ancho / n
    ancho_barra = paso * 0.66
    p = [f'<svg class="grafico" viewBox="0 0 {ancho} {alto}" role="img" aria-label="Gráfico de {escape(unidad)} por día">']
    for frac in (0, .5, 1):
        y = arriba + util * (1 - frac)
        p.append(f'<line class="g-grilla" x1="0" x2="{ancho}" y1="{y:.1f}" y2="{y:.1f}"/>')
    for i, v in enumerate(valores):
        h = max(util * v / tope, 2 if v else 0)
        x = i * paso + (paso - ancho_barra) / 2
        clase = "g-barra g-hoy" if i == len(valores) - 1 else "g-barra"
        p.append(f'<rect class="{clase}" x="{x:.1f}" y="{arriba + util - h:.1f}" width="{ancho_barra:.1f}" '
                 f'height="{h:.1f}" rx="3"><title>{escape(etiquetas[i])}: {v} {escape(unidad)}</title></rect>')
    p.append("</svg>")
    return Markup("".join(p))
