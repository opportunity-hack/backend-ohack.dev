"""Seed data for the email_templates Firestore collection.

These are the original hardcoded templates from the frontend's
src/lib/messageTemplates.js (MESSAGE_TEMPLATES). They are inserted into the
email_templates collection on first use (and by the explicit re-seed action)
so the database retains the original versions. After seeding, the database is
the source of truth -- edits happen via /api/messages/admin/templates and are
version-tracked. Do NOT edit message text here to change live emails; this
file only defines what a fresh seed/restore produces.

Generated from messageTemplates.js -- regenerate rather than hand-editing.
"""

DEFAULT_EMAIL_TEMPLATES = [{'id': 'hacker_approved',
  'title': 'Hacker Application Approved',
  'category_key': 'APPROVAL',
  'category': 'Approval & Confirmation',
  'applicable_roles': ['hacker', 'hackers'],
  'message': "🎉 You're in! Welcome to Opportunity Hack!\n"
             '\n'
             'Your hacker application is approved. Get ready to build impactful solutions for '
             'nonprofits alongside amazing teammates.\n'
             '\n'
             '🚀 Next steps:\n'
             '• Join our Slack for updates\n'
             '• Wait for us to announce nonprofit projects at '
             'https://www.ohack.dev/hack/[EVENT_ID]\n'
             "• Learn what's expected: https://www.ohack.dev/about/hackers\n"
             '• Understand judging criteria: https://www.ohack.dev/about/judges\n'
             '• Optional: Track your volunteer hours (if you want to keep track): '
             'https://www.ohack.dev/volunteer/track\n'
             '\n'
             "Let's change the world, one line of code at a time! 💻\n"
             '\n'
             'Stay connected: @opportunityhack on all socials',
  'icon': '✅'},
 {'id': 'mentor_approved',
  'title': 'Mentor Application Approved',
  'category_key': 'APPROVAL',
  'category': 'Approval & Confirmation',
  'applicable_roles': ['mentor', 'mentors'],
  'message': '🌟 Welcome to our mentor squad!\n'
             '\n'
             'Your expertise will guide teams to create life-changing solutions for nonprofits. '
             'Thank you for sharing your knowledge!\n'
             '\n'
             '📚 Resources:\n'
             '• Mentor guide: https://www.ohack.dev/about/mentors\n'
             '• **Remote mentors only**: Check in at [mentor check-in '
             'portal](https://www.ohack.dev/hack/[EVENT_ID]/mentor-checkin)\n'
             '• **In-person mentors**: Use your QR code at the venue for check-in\n'
             '• Optional: Track your volunteer hours (if you want to keep track): '
             'https://www.ohack.dev/volunteer/track\n'
             '\n'
             'Ready to inspire the next generation of changemakers? 🚀',
  'icon': '🎯'},
 {'id': 'judge_travel_confirmation',
  'title': 'Judge Application Approved - Please Confirm Travel',
  'category_key': 'APPROVAL',
  'category': 'Approval & Confirmation',
  'applicable_roles': ['judge', 'judges'],
  'message': '⚖️ Congratulations! Your judge application has been approved!\n'
             '\n'
             "We're excited to have you evaluate the innovative solutions our teams will create "
             'for nonprofits. However, we need your confirmation for an important detail:\n'
             '\n'
             '✈️ **All judging for Opportunity Hack is done IN PERSON at [LOCATION_NAME].**\n'
             '\n'
             '📍 Location Details:\n'
             '• Event location: [LOCATION_NAME]\n'
             '• More info & hotel recommendations: [LOCATION_URL]\n'
             '• Full schedule of events: https://www.ohack.dev/hack/[EVENT_ID]#countdown\n'
             '• Add yourself to our LinkedIn event: [LINKEDIN_EVENT_URL]\n'
             '\n'
             '⏰ **ACTION REQUIRED by [RSVP_DEADLINE]:**\n'
             'Please reply to this email at questions@ohack.org to confirm:\n'
             '✅ "I confirm I can attend in person at [LOCATION_NAME]" OR\n'
             '❌ "I need to decline due to travel constraints"\n'
             '\n'
             '✏️ **Need to edit your application?**\n'
             'Go to: https://www.ohack.dev/hack/[EVENT_ID]/judge-application\n'
             'Use code: "[ACCESS_CODE]"\n'
             '\n'
             'We understand travel requirements may not work for everyone. We just need to know by '
             'the deadline to finalize our judging panel.\n'
             '\n'
             'Thank you for your interest in supporting nonprofit innovation! 🌟',
  'icon': '✈️'},
 {'id': 'judge_approved',
  'title': 'Judge Application Approved',
  'category_key': 'APPROVAL',
  'category': 'Approval & Confirmation',
  'applicable_roles': ['judge', 'judges'],
  'message': '⚖️ Welcome to our judging panel!\n'
             '\n'
             'Thank you for being here! Having your talent and background to review these projects '
             'helps us to find the top teams who have solved problems for nonprofits.\n'
             '\n'
             '📋 Resources & Next Steps:\n'
             '1. Judging Intro [video](https://youtu.be/YM8j-2CA-mE?si=WNiRqI9Ww_Jd0yx0)\n'
             "2. When the projects have closed and we're ready to judge, you'll go "
             '[here](https://www.ohack.dev/judge)\n'
             '3. Judging criteria is [here](https://www.ohack.dev/about/judges)\n'
             '4. You can already start reviewing teams GitHub and DevPost now (knowing that they '
             'might land more changes before the end of the hack) all teams are listed '
             '[here](https://www.ohack.dev/hack/[EVENT_ID]#teams)\n'
             '5. All judges are listed [here](https://www.ohack.dev/hack/[EVENT_ID]#judge)\n'
             '6. Take time to say hi and introduce yourself to everyone, this is a great way to '
             'market amongst similar-minded, community focused people\n'
             '\n'
             '⏱️ Track your impact: https://www.ohack.dev/volunteer/track\n'
             '\n'
             'Ready to discover amazing innovations! ✨',
  'icon': '⚖️'},
 {'id': 'volunteer_approved',
  'title': 'Volunteer Application Approved',
  'category_key': 'APPROVAL',
  'category': 'Approval & Confirmation',
  'applicable_roles': ['volunteer', 'volunteers'],
  'message': "🙌 You're part of the dream team!\n"
             '\n'
             'Thank you for helping make Opportunity Hack magical. Every volunteer contribution '
             'creates ripple effects of positive change.\n'
             '\n'
             '⏱️ Track your impact: https://www.ohack.dev/volunteer/track\n'
             '\n'
             'Assignments coming soon. Ready to be part of something amazing? 🌟',
  'icon': '🙌'},
 {'id': 'sponsor_approved',
  'title': 'Sponsorship Approved',
  'category_key': 'APPROVAL',
  'category': 'Approval & Confirmation',
  'applicable_roles': ['sponsor', 'sponsors'],
  'message': '🤝 Partnership activated!\n'
             '\n'
             "Thank you for investing in nonprofit innovation. Together, we're amplifying social "
             'impact through technology.\n'
             '\n'
             '📈 Your support enables:\n'
             '• Free participation for nonprofits\n'
             '• Quality mentorship and resources\n'
             '• Lasting solutions for communities\n'
             '\n'
             '⏱️ Team volunteering? Track at: https://www.ohack.dev/volunteer/track',
  'icon': '🤝'},
 {'id': 'checkin_information_mentors',
  'title': 'Check-in Information',
  'category_key': 'APPROVAL',
  'category': 'Approval & Confirmation',
  'applicable_roles': ['mentor', 'mentors'],
  'message': "📱 Ready for check-in? Here's everything you need!\n"
             '\n'
             "For in-person participants, we've made check-in super easy with your personal QR "
             'code below **and also bring your identification**:\n'
             '\n'
             '[QRCode:[EVENT_ID]|[VOLUNTEER_ID]|[VOLUNTEER_TYPE]]\n'
             '\n'
             '**How to use your QR code:**\n'
             '• Simply show this QR code when you arrive at the venue\n'
             '• Our volunteers will scan it for instant check-in\n'
             '• No need to remember names, emails, or confirmation numbers!\n'
             '\n'
             '**Remote mentors**: Use the [mentor check-in '
             'portal](https://www.ohack.dev/hack/[EVENT_ID]/mentor-checkin) to check in virtually\n'
             '\n'
             "**Can't see the QR code?** No worries! You can also access it anytime from your "
             'application page:\n'
             '[View Your '
             'Application](https://www.ohack.dev/hack/[EVENT_ID]/[VOLUNTEER_TYPE]-application)\n'
             '\n'
             '📍 **Venue Information:**\n'
             'Get directions, parking details, and venue specifics at:\n'
             '[ASU Tempe Location '
             'Details](https://www.ohack.dev/about/locations/asu-tempe-arizona)\n'
             '\n'
             '🎯 **What to bring:**\n'
             '• This QR code (screenshot or bookmark this email)\n'
             '• Your laptop and charger\n'
             '• Enthusiasm for making an impact!\n'
             '\n'
             'See you soon! 🚀',
  'icon': '📱'},
 {'id': 'checkin_information',
  'title': 'Check-in Information',
  'category_key': 'APPROVAL',
  'category': 'Approval & Confirmation',
  'applicable_roles': ['hacker', 'hackers', 'judge', 'judges', 'volunteer', 'volunteers'],
  'message': "📱 Ready for check-in? Here's everything you need!\n"
             '\n'
             "For in-person participants, we've made check-in super easy with your personal QR "
             'code below **and also bring your identification**:\n'
             '\n'
             '[QRCode:[EVENT_ID]|[VOLUNTEER_ID]|[VOLUNTEER_TYPE]]\n'
             '\n'
             '**How to use your QR code:**\n'
             '• Simply show this QR code when you arrive at the venue\n'
             '• Our volunteers will scan it for instant check-in\n'
             '• No need to remember names, emails, or confirmation numbers!\n'
             '\n'
             "**Can't see the QR code?** No worries! You can also access it anytime from your "
             'application page:\n'
             '[View Your '
             'Application](https://www.ohack.dev/hack/[EVENT_ID]/[VOLUNTEER_TYPE]-application)\n'
             '\n'
             '📍 **Venue Information:**\n'
             'Get directions, parking details, and venue specifics at:\n'
             '[ASU Tempe Location '
             'Details](https://www.ohack.dev/about/locations/asu-tempe-arizona)\n'
             '\n'
             '🎯 **What to bring:**\n'
             '• This QR code (screenshot or bookmark this email)\n'
             '• Your laptop and charger\n'
             '• Enthusiasm for making an impact!\n'
             '\n'
             'See you soon! 🚀',
  'icon': '📱'},
 {'id': 'judge_application_denied',
  'title': 'Judge Application - Alternative Opportunity Available!',
  'category_key': 'DENIAL',
  'category': 'Application Denial',
  'applicable_roles': ['judge', 'judges'],
  'message': 'Thank you for your interest in judging at Opportunity Hack! 🙏\n'
             '\n'
             'While our judging panel is at capacity for this event, we have an exciting '
             'alternative that offers even more meaningful volunteer experience and community '
             'impact.\n'
             '\n'
             '🌟 **Consider becoming a MENTOR instead!**\n'
             '\n'
             "Here's why mentoring might be perfect for you:\n"
             '• **More volunteer hours** - Mentors typically contribute 8-12 hours vs 2-4 for '
             'judges\n'
             '• **Direct community impact** - Guide teams solving real nonprofit problems\n'
             '• **Professional development** - Share your expertise while learning from diverse '
             'teams\n'
             '• **Networking opportunities** - Work closely with passionate developers and '
             'nonprofit leaders\n'
             '• **Recognition** - All mentor contributions are documented for professional/visa '
             'purposes\n'
             '• **🏠 Remote-friendly** - Mentor virtually from anywhere! No travel required - '
             'support teams through Slack, video calls, and code reviews\n'
             '• **Flexible schedule** - Choose when and how much you engage throughout the '
             'hackathon weekend\n'
             '• **Deeper relationships** - Build lasting connections with teams as you guide their '
             'entire project journey\n'
             '\n'
             '📝 **Ready to make an even bigger impact?**\n'
             'Apply to be a mentor: [Mentor '
             'Application](https://www.ohack.dev/hack/[EVENT_ID]/mentor-application)\n'
             '\n'
             '⏱️ All mentoring hours can be tracked at: https://www.ohack.dev/volunteer/track\n'
             '\n'
             '🚀 **Still want to judge future events?** Keep an eye out for our next hackathon '
             'announcements!\n'
             '\n'
             "Your expertise can transform ideas into lasting solutions. We'd love to have you on "
             'our mentor team! 💡\n'
             '\n'
             'Stay connected: @opportunityhack on all socials',
  'icon': '🎯'},
 {'id': 'application_denied',
  'title': 'Application Not Approved',
  'category_key': 'DENIAL',
  'category': 'Application Denial',
  'applicable_roles': ['mentor',
                       'mentors',
                       'volunteer',
                       'volunteers',
                       'hacker',
                       'hackers',
                       'sponsor',
                       'sponsors'],
  'message': 'Thank you for wanting to join our mission! 🙏\n'
             '\n'
             "While we can't accommodate your application this time due to capacity, your interest "
             'in helping nonprofits means everything.\n'
             '\n'
             '🌟 Stay involved:\n'
             '• Apply for future events\n'
             '• Follow @opportunityhack for opportunities\n'
             '• Share our mission with your network\n'
             '\n'
             'Every action towards social good counts. We hope to work together soon! 💫',
  'icon': '💫'},
 {'id': 'hacker_waitlisted',
  'title': "You're on the Waitlist - Stay Tuned!",
  'category_key': 'WAITLIST',
  'category': 'Waitlist Management',
  'applicable_roles': ['hacker', 'hackers'],
  'message': "⏳ You're on our hacker waitlist!\n"
             '\n'
             "Thank you for your interest in Opportunity Hack! While we've reached capacity for "
             "initial registrations, we've added you to our waitlist.\n"
             '\n'
             '🔄 **What happens next:**\n'
             "• We'll complete check-in process at 9:00 AM on event day\n"
             "• If spots open up, you'll get an immediate notification\n"
             '• Keep your phone handy and stay ready to join!\n'
             '\n'
             '🎒 **Stay prepared:**\n'
             '• Keep your laptop charged and ready\n'
             '• Review the nonprofit projects: https://www.ohack.dev/hack/[EVENT_ID]#nonprofits\n'
             '• Join our Slack for real-time updates\n'
             '• Track your preparation time: https://www.ohack.dev/volunteer/track\n'
             '\n'
             'We appreciate your patience and enthusiasm for nonprofit innovation. Whether you '
             "join us this time or next, you're already part of our community! 🌟\n"
             '\n'
             'Stay connected: @opportunityhack on all socials',
  'icon': '⏳'},
 {'id': 'hacker_waitlist_accepted',
  'title': "🎉 You're In! Come Join Us Now!",
  'category_key': 'WAITLIST',
  'category': 'Waitlist Management',
  'applicable_roles': ['hacker', 'hackers'],
  'message': "🎉 Amazing news - you're off the waitlist and INTO the hackathon!\n"
             '\n'
             'A spot just opened up and we want YOU to fill it! Time to grab your laptop and join '
             'us for an incredible day of building solutions for nonprofits.\n'
             '\n'
             '📱 **Your Check-in QR Code:**\n'
             '[QRCode:[EVENT_ID]|[VOLUNTEER_ID]|[VOLUNTEER_TYPE]]\n'
             '\n'
             "🏃\u200d♂️ **Come NOW - Here's what to do:**\n"
             '• Head to the venue immediately\n'
             '• Bring this QR code for instant check-in\n'
             '• Get your laptop, charger, and enthusiasm ready!\n'
             '\n'
             '📍 **Venue Information:**\n'
             '[ASU Tempe Location '
             'Details](https://www.ohack.dev/about/locations/asu-tempe-arizona)\n'
             '\n'
             "🎯 **What's happening:**\n"
             '• Team formation is in progress\n'
             '• Nonprofit presentations are starting soon\n'
             '• Amazing prizes and impact awaiting!\n'
             '\n'
             "**Can't see the QR code?** Access it anytime at:\n"
             '[Your Application](https://www.ohack.dev/hack/[EVENT_ID]/hacker-application)\n'
             '\n'
             '⏱️ Track your impact: https://www.ohack.dev/volunteer/track\n'
             '\n'
             "Let's build something incredible together! 🚀",
  'icon': '🎉'},
 {'id': 'hacker_waitlist_full',
  'title': 'Waitlist Update - This Event is Full',
  'category_key': 'WAITLIST',
  'category': 'Waitlist Management',
  'applicable_roles': ['hacker', 'hackers'],
  'message': '💙 Thank you for your interest in Opportunity Hack!\n'
             '\n'
             "We've completed our check-in process and unfortunately don't have any remaining "
             "spots available for today's hackathon. We truly appreciate your enthusiasm and "
             'patience.\n'
             '\n'
             "🌟 **You're still part of our community:**\n"
             '• Follow us for future hackathon announcements\n'
             '• Join our Slack to stay connected with the community\n'
             '• Consider other ways to get involved with nonprofits year-round\n'
             '• Track any volunteer hours: https://www.ohack.dev/volunteer/track\n'
             '\n'
             '📅 **Future opportunities:**\n'
             '• We host multiple hackathons throughout the year\n'
             '• Volunteer opportunities at future events\n'
             '• Mentor roles for experienced developers\n'
             '• Stay updated on all events at https://www.ohack.dev\n'
             '\n'
             '💡 **Get involved now:**\n'
             '• Share our mission with your network\n'
             '• Follow our social impact stories\n'
             '• Connect with nonprofits in your area\n'
             '\n'
             'Your interest in using technology for social good means everything to us. We hope to '
             'hack together at a future event! 💫\n'
             '\n'
             'Stay connected: @opportunityhack on all socials',
  'icon': '💙'},
 {'id': 'sponsor_info_request',
  'title': 'Sponsor Information Request',
  'category_key': 'FOLLOW_UP',
  'category': 'Follow-up & Information',
  'applicable_roles': ['sponsor', 'sponsors'],
  'message': 'Excited about your sponsorship interest! 🚀\n'
             '\n'
             "Let's create a partnership that amplifies your impact and aligns with your values.\n"
             '\n'
             '💭 Quick questions:\n'
             '• Preferred involvement level?\n'
             "• Specific causes you're passionate about?\n"
             '• Would your team like to volunteer?\n'
             '\n'
             '⏱️ Team volunteers can track time: https://www.ohack.dev/volunteer/track\n'
             '\n'
             "Reply with your thoughts - we'll craft the perfect partnership! ✨",
  'icon': '📋'},
 {'id': 'mentor_checkin_reminder',
  'title': 'Mentor Check-in Reminder',
  'category_key': 'FOLLOW_UP',
  'category': 'Follow-up & Information',
  'applicable_roles': ['mentor', 'mentors'],
  'message': 'Time to check in! 👋\n'
             '\n'
             'Your guidance is transforming ideas into impact. Quick reminder:\n'
             '\n'
             '✅ Check-in: https://www.ohack.dev/hack/[EVENT_ID]/mentor-checkin\n'
             '📚 Resources: https://www.ohack.dev/about/mentors\n'
             '⏱️ Track time: https://www.ohack.dev/volunteer/track\n'
             '\n'
             'Every minute you spend mentoring creates lasting change! 🌟',
  'icon': '⏰'},
 {'id': 'judge_info_sharing',
  'title': 'Judge Information & Resources',
  'category_key': 'FOLLOW_UP',
  'category': 'Follow-up & Information',
  'applicable_roles': ['judge', 'judges'],
  'message': 'Ready to spot game-changing solutions? ⚖️\n'
             '\n'
             'Your expertise helps identify innovations that will transform nonprofit work.\n'
             '\n'
             '📚 Resources:\n'
             '0. Dates and times are [here on the hackathon '
             'page](https://www.ohack.dev/hack/[EVENT_ID]#countdown)\n'
             '1. Judging Intro [video](https://youtu.be/YM8j-2CA-mE?si=WNiRqI9Ww_Jd0yx0)\n'
             "2. When the projects have closed and we're ready to judge, you'll go "
             '[here](https://www.ohack.dev/judge)\n'
             '3. Judging criteria is [here](https://www.ohack.dev/about/judges)\n'
             '4. You can already start reviewing teams GitHub and DevPost now (knowing that they '
             'might land more changes before the end of the hack) all teams are listed '
             '[here](https://www.ohack.dev/hack/[EVENT_ID]#teams)\n'
             '5. All judges are listed [here](https://www.ohack.dev/hack/[EVENT_ID]#judge)\n'
             '6. Take time to say hi and introduce yourself to everyone, this is a great way to '
             'market amongst similar-minded, community focused people, join our judges Slack '
             'channel: [SLACK_CHANNEL]\n'
             '\n'
             '⏱️ Track your volunteer hours: https://www.ohack.dev/volunteer/track\n'
             '\n'
             'Get excited to discover the next big breakthrough! 🎯',
  'icon': '📚'},
 {'id': 'volunteer_time_tracking',
  'title': 'Volunteer Time Tracking Reminder',
  'category_key': 'FOLLOW_UP',
  'category': 'Follow-up & Information',
  'applicable_roles': ['mentor',
                       'mentors',
                       'judge',
                       'judges',
                       'volunteer',
                       'volunteers',
                       'hacker',
                       'hackers',
                       'sponsor',
                       'sponsors'],
  'message': 'Your time = Real impact! ⏱️\n'
             '\n'
             "Every hour you contribute creates ripple effects in nonprofit communities. Don't let "
             'your impact go uncounted!\n'
             '\n'
             '📊 Track at: https://www.ohack.dev/volunteer/track\n'
             '\n'
             'Why track?\n'
             '• Celebrate your contribution\n'
             '• Show sponsors our collective power\n'
             '• Inspire others to join our mission\n'
             '\n'
             "You're changing the world - let's measure it! 🌍",
  'icon': '⏱️'},
 {'id': 'hacker_team_reminder',
  'title': 'Team Formation Reminder',
  'category_key': 'FOLLOW_UP',
  'category': 'Follow-up & Information',
  'applicable_roles': ['hacker', 'hackers'],
  'message': 'Ready to find your dream team? 👥\n'
             '\n'
             'The best solutions come from diverse minds working together!\n'
             '\n'
             '🎯 Team tips:\n'
             '• 2-6 members work best\n'
             '• Mix skills: code + design + strategy\n'
             '• Track your journey: https://www.ohack.dev/volunteer/track\n'
             '\n'
             'Team formation activities start soon. Prepare to meet your future collaborators! ⚡',
  'icon': '👥'},
 {'id': 'community_announcement',
  'title': 'Community Announcement',
  'category_key': 'COMMUNITY',
  'category': 'Community Communications',
  'applicable_roles': ['community members', 'community', 'slack'],
  'message': 'Hello Opportunity Hack Community! 🌟\n'
             '\n'
             'We have some exciting news to share with all of our amazing community members who '
             'make our mission possible.\n'
             '\n'
             '📢 [Your announcement here]\n'
             '\n'
             '🙏 Thank you for being part of our community and helping us create lasting impact for '
             'nonprofits through technology.\n'
             '\n'
             '💬 Join the discussion on Slack\n'
             '🌐 Stay updated: https://www.ohack.dev\n'
             '📱 Follow us: @opportunityhack on all socials\n'
             '\n'
             "Together, we're changing the world! 💫",
  'icon': '📢'},
 {'id': 'community_newsletter',
  'title': 'Community Newsletter',
  'category_key': 'COMMUNITY',
  'category': 'Community Communications',
  'applicable_roles': ['community members', 'community', 'slack'],
  'message': '📧 Opportunity Hack Community Update\n'
             '\n'
             'Hello amazing community members! 👋\n'
             '\n'
             "Here's what's been happening in our community:\n"
             '\n'
             '🎯 **Recent Impact:**\n'
             '• [Add recent achievements]\n'
             '• [Add project highlights]\n'
             '• [Add community milestones]\n'
             '\n'
             '📅 **Upcoming Events:**\n'
             '• [Add upcoming hackathons]\n'
             '• [Add mentorship opportunities]\n'
             '• [Add community meetings]\n'
             '\n'
             '🌟 **Community Spotlight:**\n'
             '[Highlight a community member, project, or nonprofit]\n'
             '\n'
             '📚 **Resources & Opportunities:**\n'
             '• Track your volunteer hours: https://www.ohack.dev/volunteer/track\n'
             '• Explore our projects: https://www.ohack.dev\n'
             '• Join discussions on Slack\n'
             '\n'
             '💙 Thank you for being part of our mission to create lasting technology solutions for '
             'nonprofits!\n'
             '\n'
             'Stay connected: @opportunityhack on all socials',
  'icon': '📰'},
 {'id': 'community_event_reminder',
  'title': 'Event Reminder',
  'category_key': 'COMMUNITY',
  'category': 'Community Communications',
  'applicable_roles': ['community members', 'community', 'slack'],
  'message': "⏰ Don't Miss Out! Event Reminder\n"
             '\n'
             'Hey community! Just a friendly reminder about our upcoming event:\n'
             '\n'
             '📅 **[EVENT NAME]**\n'
             '🗓️ Date: [DATE]\n'
             '⏰ Time: [TIME]\n'
             '📍 Location: [LOCATION/VIRTUAL LINK]\n'
             '\n'
             '🎯 **What to Expect:**\n'
             '• [Add event highlights]\n'
             '• [Add what attendees will learn/do]\n'
             '• [Add networking opportunities]\n'
             '\n'
             '🚀 **How to Join:**\n'
             '[Add registration/join information]\n'
             '\n'
             '💡 **Why Attend:**\n'
             '• Make a real impact for nonprofits\n'
             '• Learn new technologies\n'
             '• Meet like-minded changemakers\n'
             '• Build your portfolio\n'
             '\n'
             '⏱️ Track your volunteer hours: https://www.ohack.dev/volunteer/track\n'
             '\n'
             'See you there! 🌟\n'
             '\n'
             'Questions? Reply to this email or ask in Slack.\n'
             '\n'
             'Stay connected: @opportunityhack on all socials',
  'icon': '📅'},
 {'id': 'community_thanks',
  'title': 'Community Appreciation',
  'category_key': 'COMMUNITY',
  'category': 'Community Communications',
  'applicable_roles': ['community members', 'community', 'slack'],
  'message': '🙏 A Heartfelt Thank You to Our Amazing Community!\n'
             '\n'
             'Dear Opportunity Hack Community,\n'
             '\n'
             'We wanted to take a moment to express our genuine gratitude for each and every one '
             "of you. Whether you're a developer, designer, project manager, mentor, or nonprofit "
             'advocate - you are the heart of our mission.\n'
             '\n'
             '💫 **Your Impact:**\n'
             '• [Add specific community achievements]\n'
             '• [Add nonprofit success stories]\n'
             '• [Add volunteer hour milestones]\n'
             '\n'
             '🌟 **What Makes You Special:**\n'
             '• Your passion for social good\n'
             '• Your technical expertise shared freely\n'
             '• Your dedication to helping nonprofits\n'
             '• Your collaborative spirit\n'
             '\n'
             '📈 **Looking Ahead:**\n'
             "Together, we're building a future where technology serves humanity. Every line of "
             'code, every design element, every mentoring session creates ripples of positive '
             'change.\n'
             '\n'
             '⏱️ Track your volunteer hours: https://www.ohack.dev/volunteer/track\n'
             '\n'
             '💬 Keep the conversations going on Slack - we love seeing your ideas and '
             'collaborations!\n'
             '\n'
             'With immense gratitude,\n'
             'The Opportunity Hack Team 💙\n'
             '\n'
             'Stay connected: @opportunityhack on all socials',
  'icon': '💝'}]
