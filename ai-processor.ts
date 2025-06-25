// AI Job Description Processor
// This simulates an AI service that processes job descriptions and extracts structured data

interface ProcessedJob {
  job_title: string
  category: string
  description: string
  work_model: string
  employment_type: string
  location?: {
    country: string
    state: string
    city: string
  }
  salary?: {
    min: number
    max: number
    currency: string
    is_disclosed: boolean
  }
  skills: string[]
  questions: Array<{
    type: string
    key: string
    label: string
    options: string[]
    required: boolean
  }>
}

export async function processJobDescription(description: string): Promise<ProcessedJob> {
  // Simulate AI processing delay
  await new Promise((resolve) => setTimeout(resolve, 2000))

  // AI Logic: Extract job information and determine conditional questions
  const processed = extractJobInfo(description)
  const conditionalQuestions = generateConditionalQuestions(processed, description)

  return {
    ...processed,
    questions: conditionalQuestions,
  }
}

function extractJobInfo(description: string): Omit<ProcessedJob, "questions"> {
  const lowerDesc = description.toLowerCase()

  // Extract job title (first line usually)
  const lines = description.split("\n").filter((line) => line.trim())
  const job_title = lines[0].replace(/^(senior|junior|lead|principal)\s+/i, (match) => match)

  // Determine category based on keywords
  let category = "other"
  if (
    lowerDesc.includes("react") ||
    lowerDesc.includes("developer") ||
    lowerDesc.includes("engineer") ||
    lowerDesc.includes("typescript")
  ) {
    category = "tech"
  } else if (lowerDesc.includes("sales") || lowerDesc.includes("business development")) {
    category = "sales"
  } else if (lowerDesc.includes("marketing") || lowerDesc.includes("campaign")) {
    category = "marketing"
  } else if (lowerDesc.includes("hr") || lowerDesc.includes("human resources") || lowerDesc.includes("recruiting")) {
    category = "hr"
  }

  // Determine work model
  let work_model = "onsite"
  if (lowerDesc.includes("remote")) work_model = "remote"
  else if (lowerDesc.includes("hybrid")) work_model = "hybrid"

  // Determine employment type
  let employment_type = "full-time"
  if (lowerDesc.includes("part-time")) employment_type = "part-time"
  else if (lowerDesc.includes("contract")) employment_type = "contract"
  else if (lowerDesc.includes("intern")) employment_type = "intern"

  // Extract salary if mentioned
  let salary = undefined
  const salaryMatch = description.match(/\$(\d{1,3}(?:,\d{3})*)\s*-\s*\$(\d{1,3}(?:,\d{3})*)/i)
  if (salaryMatch) {
    salary = {
      min: Number.parseInt(salaryMatch[1].replace(/,/g, "")),
      max: Number.parseInt(salaryMatch[2].replace(/,/g, "")),
      currency: "USD",
      is_disclosed: true,
    }
  }

  // Extract skills
  const skills: string[] = []
  const techSkills = [
    "react",
    "typescript",
    "javascript",
    "python",
    "java",
    "aws",
    "docker",
    "kubernetes",
    "redux",
    "node.js",
  ]
  techSkills.forEach((skill) => {
    if (lowerDesc.includes(skill.toLowerCase())) {
      skills.push(skill.charAt(0).toUpperCase() + skill.slice(1))
    }
  })

  // Default location (this would normally be extracted from the description)
  const location = {
    country: "US",
    state: "California",
    city: "San Francisco",
  }

  return {
    job_title,
    category,
    description: description.trim(),
    work_model,
    employment_type,
    location,
    salary,
    skills,
  }
}

function generateConditionalQuestions(
  jobInfo: Omit<ProcessedJob, "questions">,
  description: string,
): ProcessedJob["questions"] {
  const questions: ProcessedJob["questions"] = []
  const lowerDesc = description.toLowerCase()

  // Tech-specific questions
  if (jobInfo.category === "tech") {
    // Experience level question
    if (lowerDesc.includes("experience") || lowerDesc.includes("years")) {
      questions.push({
        type: "dropdown",
        key: "years_of_experience",
        label: "Years of Experience",
        options: ["1-3 years", "3-5 years", "5-8 years", "8+ years"],
        required: true,
      })
    }

    // Tech stack preference
    if (jobInfo.skills.length > 0) {
      questions.push({
        type: "dropdown",
        key: "primary_tech_stack",
        label: "Primary Technology Stack",
        options: jobInfo.skills.concat(["Other"]),
        required: true,
      })
    }

    // Certification question
    if (lowerDesc.includes("aws") || lowerDesc.includes("certification")) {
      questions.push({
        type: "dropdown",
        key: "certifications",
        label: "Relevant Certifications",
        options: [
          "AWS Solutions Architect",
          "AWS Developer",
          "Azure Fundamentals",
          "Google Cloud Professional",
          "None",
          "Other",
        ],
        required: false,
      })
    }

    // Visa sponsorship for tech roles
    if (lowerDesc.includes("visa") || lowerDesc.includes("sponsorship")) {
      questions.push({
        type: "dropdown",
        key: "visa_sponsorship_needed",
        label: "Do you require visa sponsorship?",
        options: ["Yes", "No", "Maybe in the future"],
        required: true,
      })
    }
  }

  // Sales-specific questions
  if (jobInfo.category === "sales") {
    // Commission preference
    if (lowerDesc.includes("commission") || lowerDesc.includes("bonus")) {
      questions.push({
        type: "dropdown",
        key: "commission_preference",
        label: "Commission Structure Preference",
        options: ["Base + Commission", "High Base, Low Commission", "Low Base, High Commission", "No Preference"],
        required: true,
      })
    }

    // Travel willingness
    if (lowerDesc.includes("travel")) {
      questions.push({
        type: "dropdown",
        key: "travel_willingness",
        label: "Travel Willingness",
        options: ["0-10%", "10-25%", "25-50%", "50%+", "No Travel"],
        required: true,
      })
    }

    // Sales experience
    questions.push({
      type: "dropdown",
      key: "sales_experience_type",
      label: "Sales Experience Type",
      options: ["B2B", "B2C", "Both", "New to Sales"],
      required: true,
    })
  }

  // Marketing-specific questions
  if (jobInfo.category === "marketing") {
    // Marketing channels
    questions.push({
      type: "dropdown",
      key: "marketing_expertise",
      label: "Primary Marketing Expertise",
      options: [
        "Digital Marketing",
        "Content Marketing",
        "Social Media",
        "Email Marketing",
        "SEO/SEM",
        "Traditional Marketing",
        "Other",
      ],
      required: true,
    })

    // Budget experience
    if (lowerDesc.includes("budget") || lowerDesc.includes("campaign")) {
      questions.push({
        type: "dropdown",
        key: "budget_experience",
        label: "Campaign Budget Experience",
        options: ["Under $10K", "$10K-$50K", "$50K-$100K", "$100K+", "No Budget Experience"],
        required: false,
      })
    }
  }

  // HR-specific questions
  if (jobInfo.category === "hr") {
    // Recruiting experience
    if (lowerDesc.includes("recruit") || lowerDesc.includes("hiring")) {
      questions.push({
        type: "dropdown",
        key: "recruiting_experience",
        label: "Recruiting Experience",
        options: [
          "Technical Recruiting",
          "Executive Search",
          "Volume Recruiting",
          "Full-cycle Recruiting",
          "New to Recruiting",
        ],
        required: true,
      })
    }

    // HR specialization
    questions.push({
      type: "dropdown",
      key: "hr_specialization",
      label: "HR Specialization",
      options: [
        "Talent Acquisition",
        "Employee Relations",
        "Compensation & Benefits",
        "Learning & Development",
        "HR Operations",
        "Generalist",
      ],
      required: true,
    })
  }

  // Remote work questions
  if (jobInfo.work_model === "remote") {
    questions.push({
      type: "dropdown",
      key: "remote_experience",
      label: "Remote Work Experience",
      options: ["Extensive (3+ years)", "Some (1-3 years)", "Limited (< 1 year)", "New to Remote Work"],
      required: true,
    })

    if (lowerDesc.includes("timezone") || lowerDesc.includes("est") || lowerDesc.includes("pst")) {
      questions.push({
        type: "dropdown",
        key: "timezone_preference",
        label: "Preferred Working Timezone",
        options: ["Eastern (EST)", "Central (CST)", "Mountain (MST)", "Pacific (PST)", "Flexible", "Other"],
        required: false,
      })
    }
  }

  // Return empty array if no special requirements detected
  return questions
}
