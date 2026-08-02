#set page(width: 210mm, height: 297mm, margin: 0mm, fill: white)
#set text(font: "Poppins", size: 10pt, fill: rgb("#241d1a"))
#set par(leading: 0.72em, justify: true)

#let sidebar-width = 79.5mm
#let ink = rgb("#241d1a")
#let sidebar = rgb("#b8b8b8")

#let email-icon = image(bytes("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><rect x='2' y='4' width='20' height='16' rx='1.5' fill='#241d1a'/><path d='M3.5 6L12 13L20.5 6' fill='none' stroke='white' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'/></svg>"), format: "svg", width: 3.8mm)
#let phone-icon = image(bytes("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path fill='#241d1a' d='M6.62 10.79a15.46 15.46 0 0 0 6.59 6.59l2.2-2.2a1 1 0 0 1 1.02-.24c1.12.37 2.33.57 3.57.57a1 1 0 0 1 1 1V20a1 1 0 0 1-1 1C10.61 21 3 13.39 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.57 3.57a1 1 0 0 1-.25 1.02z'/></svg>"), format: "svg", width: 3.8mm)
#let location-icon = image(bytes("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path fill='#241d1a' d='M12 2a7 7 0 0 0-7 7c0 5.25 7 13 7 13s7-7.75 7-13a7 7 0 0 0-7-7zm0 9.5A2.5 2.5 0 1 1 12 6a2.5 2.5 0 0 1 0 5.5z'/></svg>"), format: "svg", width: 3.8mm)

// Full-bleed structural fields.
#place(top + left, rect(width: sidebar-width, height: 297mm, fill: sidebar))
#place(
  top + left,
  dx: 55mm,
  dy: 17mm,
  rect(width: 155mm, height: 47mm, fill: ink),
)
// Name banner.
#place(
  top + left,
  dx: 85.5mm,
  dy: 34.5mm,
  text(
    fill: white,
    size: 24pt,
    weight: "regular",
    tracking: 0.12em,
  )[#upper("{{NAME}}")],
)

// Portrait with a clean white ring.
#place(
  top + left,
  dx: 11mm,
  dy: 12mm,
  box(
    width: 58mm,
    height: 58mm,
    fill: white,
    radius: 29mm,
    inset: 2mm,
  )[
    #box(
      width: 54mm,
      height: 54mm,
      radius: 27mm,
      clip: true,
    )[{{PORTRAIT}}]
  ],
)

// Sidebar contact details.
#place(
  top + left,
  dx: 11.5mm,
  dy: 88mm,
  block(width: 61mm)[
    #set text(size: 9.4pt)
    #grid(
      columns: (6mm, 1fr),
      row-gutter: 5.5mm,
      column-gutter: 0mm,
      align: (left, horizon),
      email-icon, [{{EMAIL}}],
      phone-icon, [{{PHONE}}],
      location-icon, [{{LOCATION}}],
    )
  ],
)

// Recipient and date.
#place(
  top + left,
  dx: 11.5mm,
  dy: 128.5mm,
  block(width: 63mm)[
    #text(size: 13pt, weight: "bold")[TO]
    #v(4mm)
    #line(length: 100%, stroke: 0.6pt + ink)
    #v(10mm)
    #text(size: 9.5pt, weight: 600)[{{COMPANY}}]
    #linebreak()
    #text(size: 9.5pt)[{{RECIPIENT}}]
    #v(6mm)
    #text(size: 9.5pt, weight: 600)[Date]
    #linebreak()
    #text(size: 9pt)[{{DATE}}]
  ],
)

// Main title and rule.
#place(
  top + left,
  dx: 85.5mm,
  dy: 87mm,
  block(width: 118mm)[
    #text(size: 14pt, weight: "medium")[Cover Letter]
    #v(3mm)
    #line(length: 100%, stroke: 0.6pt + ink)
  ],
)

// Measure the entire body and fail compilation before it can touch the signature.
#let letter-body = block(width: 118mm)[
  #set text(size: {{BODY_FONT_SIZE}}pt)
  #set par(leading: 0.76em, justify: true)
  #text(weight: "medium")[{{SALUTATION}}]
  #v(4mm)
  {{BODY}}
]
#place(top + left, dx: 85.5mm, dy: 105mm)[
  #context {
    let body-measure = measure(letter-body)
    if body-measure.height > 150mm {
      panic("cover letter body exceeds signature safety area")
    }
    letter-body
  }
]

// Fixed signature placement.
#place(
  top + left,
  dx: 85.5mm,
  dy: 263mm,
  block(width: 118mm)[
    #text(font: "Poppins", size: 9.5pt, weight: 600)[Sincerely,]
    #linebreak()
    #text(font: "Poppins", size: 9.5pt, weight: "regular")[{{SIGNATURE_NAME}}]
  ],
)
