"""Synthetic emails with the calendar result a person would expect.

Expectations are (status, all_day, start) where start is UTC "YYYY-MM-DDTHH:MMZ"
for timed items and "YYYY-MM-DD" for all-day ones. "Now" is 2026-09-24 12:00
Asia/Shanghai.
"""

CASES = [
    dict(id="zh_meeting_tomorrow", sender="王经理 <wang@corp.example.cn>", subject="项目评审会通知",
         sent="Thu, 24 Sep 2026 10:00:00 +0800",
         body="各位好：\n明天下午3点在3楼301会议室开项目评审会，请提前准备好演示材料。\n王",
         expect=[("confirmed", False, "2026-09-25T07:00Z")]),
    dict(id="en_flight_itinerary", sender="United Airlines <notifications@united.com>",
         subject="Your flight confirmation – ABC123", sent="Sun, 20 Sep 2026 09:00:00 -0700",
         body=("Confirmation number: ABC123\n\nFlight UA857\nWed, Sep 30, 2026\n"
               "Depart: San Francisco, CA (SFO) 11:25 AM\nArrive: Shanghai, CN (PVG) Thu, Oct 1, 2026 3:35 PM\n\n"
               "Check in online 24 hours before departure."),
         expect=[("confirmed", False, "2026-09-30T18:25Z")]),
    dict(id="en_interview_pdt", sender="Recruiting <jobs@acme.example.com>", subject="Interview scheduled",
         sent="Mon, 21 Sep 2026 16:30:00 -0700",
         body=("Hi Alex,\nYour interview with the platform team is confirmed for Friday, October 2, 2026 "
               "at 10:00 AM PDT via Zoom.\nZoom link: https://zoom.us/j/123\nBest,\nAcme Recruiting"),
         expect=[("confirmed", False, "2026-10-02T17:00Z")]),
    dict(id="zh_bill_deadline_date_only", sender="物业服务中心 <service@wuye.example.cn>", subject="缴费提醒",
         sent="Tue, 22 Sep 2026 09:12:00 +0800",
         body="尊敬的业主：您好！本季度物业费共计1,860元，请于2026年9月30日前缴纳，逾期将产生滞纳金。",
         expect=[("confirmed", True, "2026-09-30")]),
    dict(id="en_deadline_aoe", sender="Program Chairs <chairs@conf.example.org>",
         subject="Paper submission deadline reminder", sent="Fri, 18 Sep 2026 12:00:00 +0000",
         body=("Dear authors,\nThis is a reminder that full paper submissions close on October 5, 2026 "
               "at 11:59 PM AoE. No extensions will be granted."),
         expect=[("confirmed", False, "2026-10-06T11:59Z")]),
    dict(id="marketing_offer", sender="reMarkable <newsletters@email.remarkable.com>",
         subject="Ending soon: Get 3 months free on our annual plan", sent="Wed, 23 Sep 2026 07:04:00 -0600",
         body=("Our limited-time offer is coming to a close.\nHi Alex,\nJust a quick reminder that you have "
               "a few days left to get 3 months free when you choose an annual Connect plan. "
               "Offer ends September 30, 2026.\nShop now"),
         expect=[]),
    dict(id="zh_dentist_cancelled", sender="齿科诊所 <noreply@dental.example.cn>", subject="预约取消通知",
         sent="Wed, 23 Sep 2026 18:20:00 +0800",
         body="您好，您预约的2026年9月28日14:30洁牙检查已取消。如需重新预约，请致电400-000-0000。",
         expect=[("cancelled", False, "2026-09-28T06:30Z")]),
    dict(id="en_reschedule_et", sender="Jamie Chen <jamie@partner.example.com>", subject="Design review moved",
         sent="Tue, 22 Sep 2026 11:00:00 -0400",
         body=("Hi all,\nHeads up: the design review has been moved from Monday, Oct 5 at 2:00 PM ET to "
               "Tuesday, Oct 6 at 10:00 AM ET. Same Zoom link.\nThanks,\nJamie"),
         expect=[("confirmed", False, "2026-10-06T14:00Z"), ("cancelled", False, "2026-10-05T18:00Z")]),
    dict(id="hotel_tokyo_implied", sender="Booking.com <noreply@booking.com>",
         subject="Your booking is confirmed: Hotel Gracery Shinjuku", sent="Mon, 14 Sep 2026 03:00:00 +0000",
         body=("Booking number: 4829.117.302\nHotel Gracery Shinjuku, 1-19-1 Kabukicho, Shinjuku-ku, Tokyo, Japan\n"
               "Check-in: Saturday, October 10, 2026 (from 15:00)\nCheck-out: Monday, October 12, 2026 (until 11:00)"),
         expect=[("confirmed", False, "2026-10-10T06:00Z")],
         optional=[("confirmed", False, "2026-10-12T02:00Z")]),
    dict(id="receipt_no_action", sender="Apple <no_reply@email.apple.com>", subject="Your receipt from Apple",
         sent="Tue, 22 Sep 2026 08:00:00 +0000",
         body="Receipt\nDate: 22 Sep 2026\nOrder ID: MX8Q2K\niCloud+ 200GB  ¥21.00\nThank you for your purchase.",
         expect=[]),
    dict(id="invite_ics_new_york", sender="Alex Kim <alex@startup.example.com>",
         subject="Invitation: Quarterly planning @ Mon Oct 5, 2026 2pm - 3pm (EDT)",
         sent="Thu, 17 Sep 2026 14:00:00 -0400",
         body=("You have been invited to the following event.\nQuarterly planning\n"
               "Mon Oct 5, 2026 2pm – 3pm (Eastern Time - New York)\n\n[日历附件 text/calendar]\n"
               "BEGIN:VCALENDAR\nMETHOD:REQUEST\nBEGIN:VEVENT\nDTSTART;TZID=America/New_York:20261005T140000\n"
               "DTEND;TZID=America/New_York:20261005T150000\nSUMMARY:Quarterly planning\nUID:abc@google.com\n"
               "END:VEVENT\nEND:VCALENDAR"),
         expect=[("confirmed", False, "2026-10-05T18:00Z")]),
    dict(id="zh_train_12306", sender="12306 <12306@rails.com.cn>", subject="网上购票系统-用户支付通知",
         sent="Wed, 23 Sep 2026 20:15:00 +0800",
         body=("尊敬的张先生：您好！您于2026年09月23日在中国铁路客户服务中心网站(12306.cn)成功购买了1张车票，"
               "票款共计553.00元，订单号码E123456789。所购车票信息如下：1.张先生，2026年10月01日08:00开，"
               "上海虹桥站-北京南站，G2次列车，05车12F号，二等座，成人票，票价553.0元。"),
         expect=[("confirmed", False, "2026-10-01T00:00Z")]),
]
