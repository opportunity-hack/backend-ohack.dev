BTN_PRIMARY = "#3498db"
BTN_SECONDARY = "#43ff8c"
BTN_TERTIARY = "#db344d"

STYLE = """ <style>

      img {
        border: none;
        -ms-interpolation-mode: bicubic;
        max-width: 85%;
      }

      body {
        display: flex;
        flex-direction: column;
        height: 100%;
        justify-content: space-between;
        background: radial-gradient(ellipse at top, #f0ff00, transparent),
          radial-gradient(ellipse at bottom, #58cffb, transparent);
        font-family: sans-serif;
        -webkit-font-smoothing: antialiased;
        font-size: 14px;
        line-height: 1.4;
        margin: 0;
        padding: 0;
        -ms-text-size-adjust: 100%;
        -webkit-text-size-adjust: 100%;
      }

      table {
        border-collapse: separate;
        mso-table-lspace: 0pt;
        mso-table-rspace: 0pt;
        width: 100%;
      }
      table td {
        font-family: sans-serif;
        font-size: 14px;
        vertical-align: top;
      }

      /* -------------------------------------
          BODY & CONTAINER
      ------------------------------------- */

      .body {
        background-color: #f6f6f6;
        width: 100%;
      }

      /* Set a max-width, and make it display as block so it will automatically stretch to that width, but will also shrink down on a phone or something */
      .container {
        display: block;
        margin: 0 auto !important;
        /* makes it centered */
        max-width: 580px;
        padding: 10px;
        width: 580px;
      }

      /* This should also be a block element, so that it will fill 100% of the .container */
      .content {
        box-sizing: border-box;
        display: block;
        margin: 0 auto;
        max-width: 580px;
        padding: 10px;
      }

      /* -------------------------------------
          HEADER, FOOTER, MAIN
      ------------------------------------- */
      .main {
        background: #ffffff;
        border-radius: 3px;
        width: 100%;
      }

      .wrapper {
        box-sizing: border-box;
        padding: 20px;
      }

      .content-block {
        padding-bottom: 10px;
        padding-top: 10px;
      }

      .footer {
        clear: both;
        margin-top: 10px;
        text-align: center;
        width: 100%;
      }
      .footer td,
      .footer p,
      .footer span,
      .footer a {
        color: #999999;
        font-size: 12px;
        text-align: center;
      }

      /* -------------------------------------
          TYPOGRAPHY
      ------------------------------------- */
      h1,
      h2,
      h3,
      h4 {
        color: #000000;
        font-family: sans-serif;
        font-weight: 400;
        line-height: 1.4;
        margin: 0;
        margin-bottom: 30px;
      }

      h1 {
        font-size: 35px;
        font-weight: 300;
        text-align: center;
        text-transform: capitalize;
      }

      p,
      ul,
      ol {
        font-family: sans-serif;
        font-size: 14px;
        font-weight: normal;
        margin: 0;
        margin-bottom: 15px;
      }
      p li,
      ul li,
      ol li {
        list-style-position: inside;
        margin-left: 5px;
      }

      a {
        color: #3498db;
        text-decoration: underline;
      }

      /* -------------------------------------
          BUTTONS
      ------------------------------------- */
      .btn {
        box-sizing: border-box;
        width: 100%;
      }
      .btn > tbody > tr > td {
        padding-bottom: 15px;
      }
      .btn table {
        width: auto;
      }
      .btn table td {
        background-color: #ffffff;
        border-radius: 5px;
        text-align: center;
      }
      .btn a {
        background-color: #ffffff;
        border: solid 1px #3498db;
        border-radius: 5px;
        box-sizing: border-box;
        color: #3498db;
        cursor: pointer;
        display: inline-block;
        font-size: 14px;
        font-weight: bold;
        margin: 0;
        padding: 12px 25px;
        text-decoration: none;
        text-transform: capitalize;
      }

      .btn-primary table td {
        background-color: #3498db;
      }

      .btn-primary a {
        background-color: #3498db;
        border-color: #3498db;
        color: #ffffff;
      }
      .btn-tertiary table td {
        background-color: #db344d;
      }

      .btn-tertiary a {
        background-color: #db344d;
        border-color: #db344d;
        color: #ffffff;
      }
      
       .btn-secondary table td {
        background-color: #43ff8c;
      }

      .btn-secondary a {
        background-color: #43ff8c;
        border-color: #43ff8c;
        color: #000000;
      }

      /* -------------------------------------
          OTHER STYLES THAT MIGHT BE USEFUL
      ------------------------------------- */
      .last {
        margin-bottom: 0;
      }

      .first {
        margin-top: 0;
      }

      .align-center {
        text-align: center;
      }

      .align-right {
        text-align: right;
      }

      .align-left {
        text-align: left;
      }

      .clear {
        clear: both;
      }

      .mt0 {
        margin-top: 0;
      }

      .mb0 {
        margin-bottom: 0;
      }

      .preheader {
        color: transparent;
        display: none;
        height: 0;
        max-height: 0;
        max-width: 0;
        opacity: 0;
        overflow: hidden;
        mso-hide: all;
        visibility: hidden;
        width: 0;
      }

      .powered-by a {
        text-decoration: none;
      }

      hr {
        border: 0;
        border-bottom: 1px solid #f6f6f6;
        margin: 20px 0;
      }

      /* -------------------------------------
          RESPONSIVE AND MOBILE FRIENDLY STYLES
      ------------------------------------- */
      @media only screen and (max-width: 620px) {
        table.body h1 {
          font-size: 28px !important;
          margin-bottom: 10px !important;
        }
        table.body p,
        table.body ul,
        table.body ol,
        table.body td,
        table.body span,
        table.body a {
          font-size: 16px !important;
        }
        table.body .wrapper,
        table.body .article {
          padding: 10px !important;
        }
        table.body .content {
          padding: 0 !important;
        }
        table.body .container {
          padding: 0 !important;
          width: 100% !important;
        }
        table.body .main {
          border-left-width: 0 !important;
          border-radius: 0 !important;
          border-right-width: 0 !important;
        }
        table.body .btn table {
          width: 100% !important;
        }
        table.body .btn a {
          width: 100% !important;
        }
        table.body .img-responsive {
          height: auto !important;
          max-width: 100% !important;
          width: auto !important;
        }
      }

      /* -------------------------------------
          PRESERVE THESE STYLES IN THE HEAD
      ------------------------------------- */
      @media all {
        .ExternalClass {
          width: 100%;
        }
        .ExternalClass,
        .ExternalClass p,
        .ExternalClass span,
        .ExternalClass font,
        .ExternalClass td,
        .ExternalClass div {
          line-height: 100%;
        }
        .apple-link a {
          color: inherit !important;
          font-family: inherit !important;
          font-size: inherit !important;
          font-weight: inherit !important;
          line-height: inherit !important;
          text-decoration: none !important;
        }
        #MessageViewBody a {
          color: inherit;
          text-decoration: none;
          font-size: inherit;
          font-family: inherit;
          font-weight: inherit;
          line-height: inherit;
        }
        .btn-primary table td:hover {
          background-color: #34495e !important;
        }
        .btn-primary a:hover {
          background-color: #34495e !important;
          border-color: #34495e !important;
        }
      }
    </style>
"""
HTML = """<html>{content}</html>"""
HEAD = "<head> "+STYLE + " </head>"

CONTAINER = """<table class="m_-8969331859947538336email-footer" align="center" width="570" cellpadding="0" cellspacing="0" role="presentation" style="width:570px;margin:0 auto;padding:0;text-align:center">
                  <tbody><tr>
                    <td align="center" style="font-family:&quot;Nunito Sans&quot;,Helvetica,Arial,sans-serif;font-size:16px;padding:45px">
                      {content}
                    </font></td>
                  </tr>
                </tbody></table>"""

BODY = CONTAINER.format(content="""<p style="margin:0.4em 0 1.1875em;font-size:13px;line-height:1.625;color:#000000;text-align:center">
                      {main_body}
                      </p>""")
IMAGE = CONTAINER.format(
    content="""<img src="https://i.imgur.com/Ih0mbYx.png" alt="Logo" />""")

FOOTER = CONTAINER.format(content="""
<p style="margin:0.4em 0 1.1875em;font-size:13px;line-height:1.625;color:#a8aaaf;text-align:center">Opportunity Hack Inc. is a registered 501c3 nonprofit, EIN: 84-5113049, with the IRS of the United States of America</p><font color="#888888">
                      <p style="margin:0.4em 0 1.1875em;font-size:13px;line-height:1.625;color:#a8aaaf;text-align:center">
                       © 2023 Opportunity Hack
                        <br>
                          Don't like these emails?
                    <a href={link}>Unsubscribe</a>.
                      </p>
                      """)

BUTTON = """   <table role="presentation" border="0" cellpadding="0" cellspacing="0" class="btn {tag}">
                          <tbody>
                            <tr>
                              <td align="center">
                                <table role="presentation" border="0" cellpadding="0" cellspacing="0">
                                  <tbody>
                                    <tr>
                                     <td> <a 
                                          href={link} 
                                          target="_blank">
                                          {text}

                                          </a> </td>
                                    </tr>
                                  </tbody>
                                </table>
                              </td>
                            </tr>
                          </tbody>
                        </table>
                        """
