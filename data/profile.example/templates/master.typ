#set page(
  paper: "us-letter",
  margin: (x: 2.54cm, y: 2.00cm),
)

// cv-docker: managed-resume-layout-v1
// Blocks own the spacing: headings have more room above than below.
// Free page height expands all spacing proportionally, with a readable limit.
#let resume-density = "comfortable"
#let resume-space = if resume-density == "spacious" {
  (leading: 0.55em, section: 12pt, project: 9pt, note: 3pt)
} else if resume-density == "comfortable" {
  (leading: 0.5em, section: 10pt, project: 8pt, note: 2pt)
} else {
  (leading: 0.45em, section: 8pt, project: 7pt, note: 2pt)
}

#set text(font: "DejaVu Sans", size: 10pt, fill: black)
#set par(leading: resume-space.leading, justify: true, spacing: resume-space.note)
#set block(spacing: 0pt)

#let theme-blue = rgb("#00508C")

// Measure the whole CV at its actual width. Find the largest spacing factor
// that fits within the margins, without changing text size or horizontal layout.
#let resume-page(body) = context {
  let margin = if type(page.margin) == dictionary { page.margin } else {
    (left: page.margin, right: page.margin, top: page.margin, bottom: page.margin)
  }
  let width = page.width * (100% - margin.left.ratio - margin.right.ratio) - margin.left.length - margin.right.length
  let height = page.height * (100% - margin.top.ratio - margin.bottom.ratio) - margin.top.length - margin.bottom.length
  // Typst measures to the baseline; reserve room for the final line's descent.
  let target = height - (0.25em).to-absolute()
  let styled(factor) = {
    set par(leading: resume-space.leading * factor, spacing: resume-space.note * factor)
    body
  }
  let low = 1.0
  let high = 1.6
  if measure(styled(1), width: width).height < target {
    for _ in range(10) {
      let mid = (low + high) / 2
      if measure(styled(mid), width: width).height <= target {
        low = mid
      } else {
        high = mid
      }
    }
  }
  styled(low)
}

// Profile highlights continue the paragraph's line rhythm, including wrapping.
#let profile-note(content) = context {
  let factor = par.leading.to-absolute() / resume-space.leading.to-absolute()
  block(
    above: resume-space.leading * factor,
    below: 0pt,
    breakable: false,
    {
      set par(justify: false)
      par(content)
    },
  )
}

#let section(title) = context {
  let factor = par.leading.to-absolute() / resume-space.leading.to-absolute()
  block(
    width: 100%,
    above: resume-space.section * factor,
    below: resume-space.leading * factor,
    breakable: false,
    sticky: true,
    {
      set par(justify: false)
      stack(
        dir: ttb,
        spacing: 1.5pt * factor,
        text(fill: theme-blue, weight: "bold", size: 11pt, hyphenate: false)[#upper(title)],
        line(length: 100%, stroke: 0.5pt + theme-blue),
      )
    },
  )
}

#let project(title, first: false) = context {
  let factor = par.leading.to-absolute() / resume-space.leading.to-absolute()
  block(
    above: if first { 0pt } else { resume-space.project * factor },
    below: 2pt * factor,
    breakable: false,
    sticky: true,
    {
      set par(justify: false)
      text(fill: theme-blue, weight: "bold", hyphenate: false)[#title]
    },
  )
}

// Match the gap between bullets to the leading inside wrapped bullets.
#let bullet(content) = context {
  let factor = par.leading.to-absolute() / resume-space.leading.to-absolute()
  let margin = if type(page.margin) == dictionary { page.margin } else {
    (left: page.margin, right: page.margin, top: page.margin, bottom: page.margin)
  }
  let width = page.width * (100% - margin.left.ratio - margin.right.ratio) - margin.left.length - margin.right.length
  let height = page.height * (100% - margin.top.ratio - margin.bottom.ratio) - margin.top.length - margin.bottom.length
  let body = grid(
    columns: (12pt, 1fr),
    gutter: 0pt,
    align: (right, left),
    [•#h(4pt)],
    content,
  )
  let oversized = measure(body, width: width).height > height
  block(
    above: resume-space.leading * factor,
    below: 0pt,
    // Normal bullets stay together; oversized bullets may flow across pages.
    breakable: oversized,
    // Typst's default bottom text edge is the baseline. Reserve descent when
    // a long bullet fills a page so its last glyphs stay inside the margins.
    inset: if oversized { (bottom: 0.25em) } else { 0pt },
    body,
  )
}

#show: resume-page

// Header
{{CONTACT_HEADER}}

#section("Profile")
*Replace this paragraph with a concise summary of your background, strengths, and target role.*

#profile-note[#strong[Optional academic highlights or domain summary]]

#section("Education")
#strong[University Name] | Degree Name | #emph[YYYY - YYYY]
#bullet[#strong[Honors:] Optional honors or distinctions]
#bullet[#strong[Relevant Coursework:] Course A, Course B, Course C]

#section("Projects")
#project("Representative Project | Stack | Year", first: true)
#bullet[Summarize the technical scope in one sentence.]
#bullet[Call out a concrete implementation detail, tool, or algorithm.]
#bullet[Keep claims factual and avoid private personal details.]

#project("Second Project | Stack | Year")
#bullet[Describe the most relevant engineering work.]
#bullet[Use specific systems, libraries, hardware, or protocols where helpful.]
#bullet[Prefer measured constraints or verified facts over hype.]

#section("Skills")
#bullet[#strong[Languages:] Python, C++, C, Bash]
#bullet[#strong[Systems:] Linux, networking, debugging, virtualization]
#bullet[#strong[Tools:] Docker, Git, test tooling, hardware or cloud tools]

#section("Additional Experience")
#bullet[#strong[YYYY - YYYY] | Example service, volunteering, or community work]
