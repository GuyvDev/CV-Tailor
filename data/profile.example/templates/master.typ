#set document(title: "Candidate Resume Template")
#set page(
  paper: "us-letter",
  margin: (x: 2.54cm, y: 2.54cm),
)
#set text(font: "Calibri", size: 11pt, fill: black)

#set par(leading: 0.5em, justify: true, spacing: 0pt)
#set block(spacing: 6pt)

#let theme-blue = rgb("#00508C")
#let black = rgb("#000000")

#let section(title) = {
  v(8pt)
  text(fill: theme-blue, weight: "bold", size: 12pt)[#upper(title)]
  v(-4pt)
  line(length: 100%, stroke: 0.5pt + theme-blue)
  v(3pt)
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

#section("Profile")
*Replace this paragraph with a concise summary of your background, strengths, and target role.*

#v(1fr)
#strong[Optional academic highlights or domain summary]

#section("Education")
#strong[University Name] | Degree Name | #emph[YYYY - YYYY]
#bullet[#strong[Honors:] Optional honors or distinctions]
#bullet[#strong[Relevant Coursework:] Course A, Course B, Course C]

#section("Projects")
#project("Representative Project | Stack | Year")
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
