"""Generates the demo resources in sample_data/ (PDF, DOCX, TXT, HTML + pasted text via manifest.json).

The documents are ILLUSTRATIVE teaching material written for the CONVERGE AI demo. They deliberately contain
(a) repeated facts phrased differently, (b) details found in only one source and (c) a few disagreeing figures,
so the synthesis behaviour can be inspected. Do not cite them as real research.
"""
import json
from pathlib import Path

from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

OUT = Path(__file__).resolve().parent.parent / "sample_data"
OUT.mkdir(exist_ok=True)
NOTE = "Sample document written for the CONVERGE AI demo - figures are illustrative, not authoritative."

# ---------------------------------------------------------------- 1. PDF (3 pages, textbook style)
pdf_pages = [
    [("1. Introduction", "A lithium-ion battery is a rechargeable electrochemical energy storage device. During discharge, lithium ions move from the negative electrode (anode) through an electrolyte to the positive electrode (cathode); charging reverses the flow. The technology was commercialised by Sony in 1991, building on the work of John Goodenough, Stanley Whittingham and Akira Yoshino, who shared the 2019 Nobel Prize in Chemistry. Today lithium-ion cells power phones, laptops, electric vehicles and grid storage systems."),
     ("2. Cell components", "Every cell contains four essential components. The anode is usually graphite. The cathode is a lithium metal oxide or phosphate. The electrolyte is a lithium salt, typically LiPF6, dissolved in organic carbonate solvents. A porous polymer separator keeps the electrodes apart to prevent short circuits while still letting lithium ions pass through."),
     ("3. Cathode chemistries", "Common cathode families are lithium cobalt oxide (LCO), nickel manganese cobalt oxide (NMC), nickel cobalt aluminium oxide (NCA), lithium iron phosphate (LFP) and lithium manganese oxide (LMO). NMC cells offer high energy density of roughly 200-250 Wh/kg at cell level. LFP cells have a lower energy density of about 160 Wh/kg, but they last longer, are more thermally stable and contain no cobalt.")],
    [("4. Performance metrics", "The key metrics are energy density (Wh/kg), specific power, cycle life and C-rate. Cycle life is the number of full charge-discharge cycles a cell completes before its capacity falls to 80% of the original value. NMC cells typically deliver 1,000-2,000 cycles, while LFP cells reach 2,000-4,000 cycles. The nominal voltage is about 3.6-3.7 V for NMC cells and 3.2 V for LFP cells."),
     ("5. Safety and thermal runaway", "Lithium-ion cells can fail through thermal runaway, a self-accelerating chain of exothermic reactions that begins when the internal temperature rises beyond roughly 150 °C. A battery management system (BMS) monitors voltage and temperature of every cell group and disconnects the pack if limits are exceeded. Separators with a shutdown layer add a second line of defence.")],
    [("6. Cost trends", "Battery pack prices have fallen by roughly 90% since 2010. BloombergNEF reported an average pack price of $139 per kWh in 2023, a level that brings electric vehicles close to price parity with petrol cars in several markets."),
     ("7. Outlook", "Solid-state batteries replace the liquid electrolyte with a solid one and may reach energy densities of up to 400 Wh/kg. Their commercial introduction is expected by 2027. Sodium-ion cells are being developed as a lower-cost complement for stationary storage, since sodium is far more abundant than lithium.")],
]
hs = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=15, spaceAfter=8, spaceBefore=12)
bs = ParagraphStyle("b", fontName="Helvetica", fontSize=11, leading=16, spaceAfter=8)
doc = SimpleDocTemplate(str(OUT / "li_ion_fundamentals.pdf"), pagesize=A4, title="Lithium-Ion Batteries: Fundamentals")
story = []
for pi, page in enumerate(pdf_pages):
    story.append(Paragraph("Lithium-Ion Batteries: Fundamentals (course notes)", ParagraphStyle("run", fontName="Helvetica", fontSize=8)))
    for h, b in page:
        story += [Paragraph(h, hs), Paragraph(b, bs)]
    if pi < len(pdf_pages) - 1:
        story.append(PageBreak())
doc.build(story)

# ---------------------------------------------------------------- 2. DOCX (industry report with table)
d = Document()
d.core_properties.title = "Lithium-Ion Batteries in Electric Vehicles"
d.add_heading("Lithium-Ion Batteries in Electric Vehicles", 0)
d.core_properties.comments = NOTE
d.add_heading("Why EVs use lithium-ion", 1)
d.add_paragraph("Electric vehicles rely on lithium-ion packs because the chemistry stores more energy per kilogram than lead-acid or nickel-metal-hydride alternatives and can be recharged thousands of times. A typical passenger-car pack stores between 40 and 100 kWh and delivers a range of 300-600 km. Inside each cell, lithium ions shuttle between a graphite anode and a metal-oxide or phosphate cathode through a liquid electrolyte, with a thin separator preventing contact between the electrodes.")
d.add_heading("Chemistry choices by vehicle type", 1)
d.add_paragraph("Manufacturers pick the chemistry to match the product. Long-range models favour NMC for its energy density, while entry-level models increasingly use lithium iron phosphate (LFP), which is cheaper, avoids cobalt and tolerates frequent full charging. BYD's Blade battery is an LFP design, and Tesla fits LFP packs in its standard-range Model 3.")
t = d.add_table(rows=1, cols=4)
t.style = "Table Grid"
for i, h in enumerate(["Chemistry", "Energy density (Wh/kg)", "Cycle life", "Typical use"]):
    t.rows[0].cells[i].text = h
for row in [("NMC", "200-250", "1,000-2,000 cycles", "Long-range passenger EVs"),
            ("LFP", "140-180", "3,000-6,000 cycles", "Standard-range EVs, buses, grid storage"),
            ("NCA", "220-260", "500-1,000 cycles", "Performance EVs")]:
    cells = t.add_row().cells
    for i, v in enumerate(row):
        cells[i].text = v
d.add_heading("Charging and lifetime", 1)
d.add_paragraph("Modern EVs accept DC fast charging at 150-250 kW, which restores 10% to 80% of the battery in about 20-30 minutes. Frequent fast charging at high temperature accelerates ageing. Most manufacturers warrant the battery for 8 years or 160,000 km, guaranteeing at least 70% of the original capacity.")
d.add_heading("Costs", 1)
d.add_paragraph("According to this report's market survey, the average battery pack price was $151 per kWh in 2023, down from over $1,200 per kWh in 2010. Cell cost is dominated by the cathode materials, which is why cobalt-free chemistries are attractive.")
d.add_heading("Second life", 1)
d.add_paragraph("Packs that fall below 70-80% of their original capacity are no longer ideal for driving but can serve a second life in stationary storage for another 7-10 years before recycling.")
d.save(OUT / "ev_applications_report.docx")

# ---------------------------------------------------------------- 3. TXT (lab notes)
(OUT / "lab_notes_degradation.txt").write_text(f"""# Cell degradation - lab notes

## Formation and the SEI layer
During the first charge cycles a solid electrolyte interphase (SEI) forms on the graphite anode. The SEI consumes some of the cyclable lithium, so a new cell typically loses 5-10% of its capacity during formation. A stable SEI protects the electrolyte from further decomposition, but it keeps growing slowly, which is a main cause of capacity fade.

## Main ageing mechanisms
- Lithium plating: charging below 0 °C or at a high C-rate deposits metallic lithium on the anode surface, which permanently removes lithium and can grow dendrites that cause internal short circuits.
- Cathode cracking: repeated expansion and contraction fractures particles of nickel-rich cathodes and exposes fresh surface to the electrolyte.
- Calendar ageing: cells age even when idle, and storage at a high state of charge speeds this up. Keeping the state of charge between 20% and 80% extends life.
- Heat: as a rule of thumb, each 10 °C rise in temperature roughly doubles the rate of chemical degradation. The best operating temperature is 20-25 °C.

## Thermal safety
Exothermic decomposition of the SEI layer starts at about 80 °C, well before full thermal runaway. If the heat cannot escape, the separator melts, the electrodes short, and the cell vents flammable electrolyte vapour.

## Our cycling results
In our tests LFP cells retained 80% of their capacity after 3,200 cycles, while NMC cells reached the same 80% threshold after about 1,400 cycles at 25 °C and 1C charge/discharge.
""", encoding="utf-8")

# ---------------------------------------------------------------- 4. HTML (news article)
(OUT / "solid_state_news.html").write_text(f"""<!doctype html><html><head><meta name="description" content="{NOTE}"><title>Solid-state batteries: hype or breakthrough?</title>
<meta property="og:title" content="Solid-state batteries: hype or breakthrough?"><meta name="author" content="Demo Newsroom"></head>
<body><nav>Home | News | About</nav><article>
<h1>Solid-state batteries: hype or breakthrough?</h1>
<h2>What changes</h2>
<p>A solid-state battery swaps the flammable liquid electrolyte of a conventional lithium-ion cell for a solid material such as a sulfide, an oxide ceramic or a polymer. Developers say this can raise energy density to as much as 400 Wh/kg, improve safety because there is no flammable liquid, and allow faster charging.</p>
<h2>The obstacles</h2>
<p>Lithium dendrites can still penetrate brittle ceramic electrolytes, the contact between solid layers creates high interface resistance, and manufacturing at scale remains expensive. Sulfide electrolytes are also sensitive to moisture and release toxic hydrogen sulfide when damaged.</p>
<h2>Timelines</h2>
<p>Toyota has said it hopes to start limited production of solid-state cells around 2027-2028. However, most independent analysts do not expect mass-market solid-state batteries before 2030, and some believe the cost will stay above that of today's lithium-ion cells for years after launch.</p>
<p>For example, early solid-state cells are likely to appear first in premium vehicles and consumer electronics, where a higher price can be absorbed.</p>
</article><footer>Subscribe to our newsletter. Copyright demo newsroom.</footer></body></html>""", encoding="utf-8")

# ---------------------------------------------------------------- 5. pasted analyst note
PASTE = f"""Analyst briefing: costs, materials and recycling

Every lithium-ion cell is built from an anode, a cathode, an electrolyte and a separator. BloombergNEF put the global average battery pack price at $139 per kWh in 2023, roughly 90% lower than in 2010. Lithium carbonate prices dropped sharply during 2023 after the spike of 2022, which helped pack prices.

Recycling is becoming a strategic issue. Hydrometallurgical processes can recover more than 95% of the cobalt and nickel from spent cells, and recovered lithium can be returned to cathode production. The EU Battery Regulation requires minimum recycled content in new batteries from 2031: 16% for cobalt, 6% for lithium and 6% for nickel.

Cobalt supply is concentrated, with most mined in the Democratic Republic of the Congo, so many manufacturers are moving to cobalt-free LFP chemistries. Analysts also note that thermal runaway remains the main safety concern, which is why pack designers add cooling plates and thermal barriers between cells."""

manifest = {
    "name": "Lithium-Ion Battery Technology",
    "description": "How lithium-ion batteries work, how they are used in EVs, how they degrade, what comes next (solid-state) and what they cost. "
                   "Demo workspace: five illustrative sources with overlapping facts, unique details and a few disagreements.",
    "resources": [
        {"file": "li_ion_fundamentals.pdf", "name": "Li-ion Fundamentals (course notes).pdf"},
        {"file": "ev_applications_report.docx", "name": "EV Applications Report.docx"},
        {"file": "lab_notes_degradation.txt", "name": "Lab notes - degradation.txt"},
        {"file": "solid_state_news.html", "name": "Solid-state batteries article.html"},
        {"name": "Analyst briefing (pasted text)", "text": PASTE},
    ],
}
(OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print("sample data written to", OUT)
