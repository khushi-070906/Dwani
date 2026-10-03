"""Email texts. First line is the subject. {placeholders} must stay.
Sent from the portal's own Gmail (SMTP_USER)."""

TEMPLATES = {
    "received": """We've received your application ({role_title})
Hi {first_name},

Thank you for applying for the {role_title} internship at {org_name}. Your application has been received and will be reviewed by our team.

You can check the status of your application at any time here:
{status_url}

We usually get back to every applicant within 1-2 weeks.

Best regards,
{signatory_name}
{signatory_title}, {org_name}
""",
    "interview": """Interview invitation: {role_title} internship at {org_name}
Hi {first_name},

Thank you for your interest in the {role_title} internship. We would like to invite you for a short conversation with our team.

Date & time: {when}
Duration: about {duration} minutes
Where: {where}

A calendar invite is attached. If this time doesn't work for you, please reply with two or three alternatives.

Your application status: {status_url}

Best regards,
{signatory_name}
{signatory_title}, {org_name}
""",
    "offer": """Internship offer: {role_title} at {org_name}
Hi {first_name},

Congratulations! We are pleased to offer you an internship at {org_name} as a {role_title}, from {start} to {end}.

Please find your offer letter attached (Ref. {doc_id}). Its authenticity can be verified at:
{verify_url}

We look forward to having you on the team. Joining details will follow before your start date. If you have any questions in the meantime, simply reply to this email.

Best regards,
{signatory_name}
{signatory_title}, {org_name}
""",
    "rejection": """Your application to {org_name}
Hi {first_name},

Thank you for applying for the {role_title} internship at {org_name}, and for the time you put into your application.

After careful consideration, we will not be moving forward with your application at this time. We received many strong applications for a limited number of positions, and this decision is not a reflection of your potential. We encourage you to apply again in a future round.

We wish you the very best.

Best regards,
{signatory_name}
{signatory_title}, {org_name}
""",
    "certificate": """Your internship certificate from {org_name}
Hi {first_name},

Thank you for your work as a {role_title} at {org_name} from {start} to {end}. It was a pleasure having you on the team.

Your Internship Completion Certificate is attached (No. {doc_id}). Anyone can verify it here:
{verify_url}

To add it to your LinkedIn profile in one click:
{linkedin_url}

We wish you all the best for the future. Do stay in touch!

Best regards,
{signatory_name}
{signatory_title}, {org_name}
""",
    "lor": """Letter of recommendation from {org_name}
Hi {first_name},

As requested, please find attached a letter of recommendation for your internship as a {role_title} at {org_name} (Ref. {doc_id}).

It can be verified at: {verify_url}

Best of luck with your applications!

Best regards,
{signatory_name}
{signatory_title}, {org_name}
""",
}
