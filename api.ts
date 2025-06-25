// Client-side API functions - these call server-side API routes

export async function getJobs(): Promise<any[]> {
  try {
    const response = await fetch("/api/jobs")
    if (!response.ok) {
      throw new Error(`Error: ${response.status}`)
    }
    const data = await response.json()
    return data.jobs || []
  } catch (error) {
    console.error("Failed to load jobs:", error)
    return []
  }
}

export async function getJob(id: string): Promise<any> {
  try {
    const response = await fetch(`/api/jobs/${id}`)
    if (!response.ok) {
      throw new Error(`Error: ${response.status}`)
    }
    const data = await response.json()
    return data.job
  } catch (error) {
    console.error("Failed to load job:", error)
    return null
  }
}

export async function submitApplication(jobId: string, applicationData: any): Promise<any> {
  // Create FormData to send to the API
  const formData = new FormData()
  formData.append("jobId", jobId)
  formData.append("applicantName", applicationData.applicant_name)
  formData.append("email", applicationData.email)
  formData.append("phone", applicationData.phone || "")
  formData.append("coverLetter", applicationData.cover_letter || "")
  formData.append("conditionalAnswers", JSON.stringify(applicationData.conditional_answers || {}))

  // Add resume file if available
  if (applicationData.resume_file) {
    formData.append("resumeFile", applicationData.resume_file)
  }

  try {
    // Send the form data to our API route
    const response = await fetch("/api/applications/submit", {
      method: "POST",
      body: formData,
    })

    if (!response.ok) {
      const errorData = await response.json()
      throw new Error(errorData.message || `Error: ${response.status}`)
    }

    const result = await response.json()
    return result
  } catch (error) {
    console.error("Error submitting application:", error)
    throw error
  }
}

export async function getApplications(jobId?: string): Promise<any[]> {
  try {
    // Use the API route to get applications
    const url = jobId ? `/api/applications?jobId=${jobId}` : "/api/applications"
    const response = await fetch(url)

    if (!response.ok) {
      throw new Error(`Error: ${response.status}`)
    }

    const data = await response.json()
    return data.applications || []
  } catch (error) {
    console.error("Error getting applications:", error)
    return []
  }
}
