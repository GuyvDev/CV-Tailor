#set page(
  paper: "us-letter",
  margin: (x: 2.54cm, y: 2.00cm),
)

#set text(font: "DejaVu Sans", size: 10pt, fill: black)
#set par(leading: 0.5em, justify: true, spacing: 0pt)
#set block(spacing: 6pt)

#let theme-blue = rgb("#00508C")

#let section(title) = {
  stack(
    dir: ttb,
    spacing: 1pt,
    text(fill: theme-blue, weight: "bold", size: 11pt)[#upper(title)],
    line(length: 100%, stroke: 0.5pt + theme-blue),
  )
  v(4pt)
}

#let project(title) = {
  v(4pt)
  text(fill: theme-blue, weight: "bold")[#title]
}

#let bullet(content) = {
  grid(
    columns: (12pt, 1fr),
    gutter: 0pt,
    align: (right, left),
    [•#h(4pt)],
    content
  )
}

// Header
{{CONTACT_HEADER}}

#v(8pt)

{{PROFILE_SECTION}}

{{BODY_CONTENT}}
