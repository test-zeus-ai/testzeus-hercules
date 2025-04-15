from typing import Dict, Any
from datetime import datetime
from lxml import etree
import json
import uuid



class HTMLGenerator:

    def __init__(self):
        pass

    def parse_junit_xml(self, xml_file: str) -> list:
        testcases_data = []
        context = etree.iterparse(xml_file, events=('end',), tag='testcase')
        for event, elem in context:
            testcase_info = {
                "name": elem.get("name") or "",
                "classname": elem.get("classname") or "",
                "time": float(elem.get("time") or 0),
                "failure": None,
                "system_out": [],
                "properties": {}
            }

            # Parse <failure> if present
            failure = elem.find('failure')
            if failure is not None:
                testcase_info["failure"] = failure.get("message") or ""

            # Parse all <system-out>
            for sys_out in elem.findall('system-out'):
                if sys_out.text:
                    testcase_info["system_out"].append(sys_out.text.strip())

            # Parse <properties>
            properties = elem.find('properties')
            if properties is not None:
                for prop in properties.findall('property'):
                    name = prop.get("name")
                    value = prop.get("value")
                    if name:
                        clean_name = name.replace(" ", "_").replace("-", "_").replace(",", "").lower().strip()
                        testcase_info["properties"][clean_name] = value or ""

            testcases_data.append(testcase_info)

            # Free memory
            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]

        return testcases_data
    

    def build_html(self, test_data: list) -> None:
        # Calculate summary statistics
        passed_count = len([t for t in test_data if not t.get("failure")])
        failed_count = len(test_data) - passed_count
        pass_rate = round((passed_count / len(test_data)) * 100) if test_data else 0
        total_duration = round(sum(t.get("time", 0) for t in test_data))
        
        # Generate table rows and JS details
        table_rows = ""
        js_details_dict = {}

        for result in test_data:
            tag = uuid.uuid4().hex
            result_class = "passed" if not result.get("failure") else "failed"
            result_text = "Passed" if not result.get("failure") else "Failed"
            
            table_rows += f"""
            <tr class="{result_class}">
                <td class="details-control">{result.get("name", "N/A")}</td>
                <td>{tag}</td>
                <td><span class="{result_class}">{result_text}</span></td>
                <td>{result.get("time", "N/A")}</td>
            </tr>
            """
            
            # Prepare details data
            js_details_dict[tag] = {
                "name": result.get("name", "N/A"),
                "classname": result.get("classname", "N/A"),
                "failure": result.get("failure"),
                "system_out": result.get("system_out", []),
                "terminate": result.get("properties", {}).get("terminate", "N/A"),
                "feature_file": result.get("properties", {}).get("feature_file", "N/A"),
                "output_file": result.get("properties", {}).get("output_file", "N/A"),
                "proofs_video": result.get("properties", {}).get("proofs_video", "N/A"),
                "proofs_base_folder_includes_screenshots_recording_netwrok_logs_api_logs_sec_logs_accessibility_logs": 
                    result.get("properties", {}).get("proofs_base_folder_includes_screenshots_recording_netwrok_logs_api_logs_sec_logs_accessibility_logs", "N/A"),
                "proofs_screenshot": result.get("properties", {}).get("proofs_screenshot", "N/A"),
                "network_logs": result.get("properties", {}).get("network_logs", "N/A"),
                "agents_internal_logs": result.get("properties", {}).get("agents_internal_logs", "N/A"),
                "planner_thoughts": result.get("properties", {}).get("planner_thoughts", "N/A"),
                "plan": result.get("properties", {}).get("plan", "N/A"),
                "next_step": result.get("properties", {}).get("next_step", "")
            }

        # Convert JS details to a JavaScript object literal string
        js_details = ",\n        ".join([
            f'"{tag}": {json.dumps(data, indent=2)}' for tag, data in js_details_dict.items()
        ])

        # Generate HTML content
        self.html_content = f"""<!DOCTYPE html>
    <html lang="en">
    <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Interactive Test Results</title>
    <link rel="stylesheet" href="https://cdn.datatables.net/1.11.5/css/jquery.dataTables.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        :root {{
        --primary-color: #3498db;
        --success-color: #2ecc71;
        --danger-color: #e74c3c;
        --warning-color: #f39c12;
        --light-color: #f8f9fa;
        --dark-color: #343a40;
        --border-color: #dee2e6;
        --text-color: #495057;
        --text-light: #6c757d;
        }}
        
        body {{
        font-family: 'Roboto', sans-serif;
        margin: 0;
        padding: 20px;
        background-color: #f5f7fa;
        color: var(--text-color);
        line-height: 1.6;
        }}
        
        .container {{
        max-width: 1200px;
        margin: 0 auto;
        background: white;
        border-radius: 8px;
        box-shadow: 0 0 20px rgba(0, 0, 0, 0.05);
        padding: 30px;
        }}
        
        .header {{
        margin-bottom: 30px;
        padding-bottom: 20px;
        border-bottom: 1px solid var(--border-color);
        }}
        
        h1 {{
        font-size: 28px;
        font-weight: 600;
        color: var(--dark-color);
        margin: 0 0 10px 0;
        }}
        
        .report-meta {{
        display: flex;
        flex-wrap: wrap;
        gap: 20px;
        margin-bottom: 20px;
        }}
        
        .meta-item {{
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 14px;
        color: var(--text-light);
        }}
        
        .meta-item i {{
        color: var(--primary-color);
        }}
        
        .summary-cards {{
        display: flex;
        gap: 20px;
        margin-bottom: 30px;
        flex-wrap: wrap;
        }}
        
        .summary-card {{
        flex: 1;
        min-width: 200px;
        background: white;
        border-radius: 6px;
        padding: 20px;
        box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05);
        border-top: 4px solid var(--primary-color);
        }}
        
        .summary-card.passed {{
        border-top-color: var(--success-color);
        }}
        
        .summary-card.failed {{
        border-top-color: var(--danger-color);
        }}
        
        .summary-card .value {{
        font-size: 28px;
        font-weight: 700;
        margin: 10px 0;
        }}
        
        .summary-card.passed .value {{
        color: var(--success-color);
        }}
        
        .summary-card.failed .value {{
        color: var(--danger-color);
        }}
        
        .summary-card .label {{
        font-size: 14px;
        color: var(--text-light);
        text-transform: uppercase;
        letter-spacing: 0.5px;
        }}
        
        .filter-box {{
        background: var(--light-color);
        padding: 15px;
        border-radius: 6px;
        margin-bottom: 20px;
        display: flex;
        align-items: center;
        gap: 20px;
        flex-wrap: wrap;
        }}
        
        .filter-box label {{
        display: flex;
        align-items: center;
        gap: 8px;
        cursor: pointer;
        font-size: 14px;
        margin: 0;
        }}
        
        .filter-box input[type="checkbox"] {{
        width: 16px;
        height: 16px;
        accent-color: var(--primary-color);
        }}
        
        .passed {{
        background-color: rgba(46, 204, 113, 0.1);
        }}
        
        .failed {{
        background-color: rgba(231, 76, 60, 0.1);
        }}
        
        td.details-control {{
        cursor: pointer;
        color: var(--primary-color);
        font-weight: 500;
        position: relative;
        padding-left: 20px;
        }}
        
        td.details-control:before {{
        content: "+";
        position: absolute;
        left: 5px;
        font-weight: bold;
        transition: transform 0.2s;
        }}
        
        tr.shown td.details-control:before {{
        content: "-";
        }}
        
        .details-content {{
        padding: 20px;
        background: white;
        border: 1px solid var(--border-color);
        border-radius: 6px;
        margin: 10px 0;
        box-shadow: 0 2px 5px rgba(0, 0, 0, 0.05);
        }}
        
        .details-content strong {{
        color: var(--dark-color);
        display: inline-block;
        min-width: 250px;
        }}
        
        .details-content pre {{
        background: var(--light-color);
        padding: 10px;
        border-radius: 4px;
        overflow-x: auto;
        white-space: pre-wrap;
        margin: 10px 0;
        }}
        
        /* DataTables customization */
        .dataTables_wrapper .dataTables_filter input {{
        border: 1px solid var(--border-color);
        border-radius: 4px;
        padding: 5px 10px;
        }}
        
        .dataTables_wrapper .dataTables_length select {{
        border: 1px solid var(--border-color);
        border-radius: 4px;
        padding: 5px;
        }}
        
        .dataTables_wrapper .dataTables_paginate .paginate_button {{
        border: 1px solid var(--border-color) !important;
        border-radius: 4px !important;
        margin: 0 3px;
        }}
        
        .dataTables_wrapper .dataTables_paginate .paginate_button.current {{
        background: var(--primary-color) !important;
        color: white !important;
        border: none !important;
        }}
        
        @media (max-width: 768px) {{
        .container {{
            padding: 15px;
        }}
        
        .summary-cards {{
            flex-direction: column;
        }}
        
        .summary-card {{
            min-width: 100%;
        }}
        }}
    </style>
    </head>
    <body>

    <div class="container">
        <div class="header">
        <h1><i class="fas fa-clipboard-check"></i> Test Results Dashboard</h1>
        
        <div class="report-meta">
            <div class="meta-item">
            <i class="fas fa-calendar-alt"></i>
            <span>Report generated on <strong>{datetime.now()}</strong></span>
            </div>
            <div class="meta-item">
            <i class="fas fa-stopwatch"></i>
            <span>Total duration: <strong>{total_duration} seconds</strong></span>
            </div>
        </div>
        
        <div class="summary-cards">
            <div class="summary-card">
            <div class="label">Total Tests</div>
            <div class="value">{len(test_data)}</div>
            </div>
            <div class="summary-card passed">
            <div class="label">Passed</div>
            <div class="value">{passed_count}</div>
            </div>
            <div class="summary-card failed">
            <div class="label">Failed</div>
            <div class="value">{failed_count}</div>
            </div>
            <div class="summary-card">
            <div class="label">Pass Rate</div>
            <div class="value" style="color: {'#2ecc71' if pass_rate >= 80 else '#f39c12' if pass_rate >= 50 else '#e74c3c'};">{pass_rate}%</div>
            </div>
        </div>
        </div>

        <div class="filter-box">
        <div>
            <strong>Filter Results:</strong>
        </div>
        <label><input type="checkbox" class="result-filter" value="Passed" checked> <span class="passed">Passed</span></label>
        <label><input type="checkbox" class="result-filter" value="Failed" checked> <span class="failed">Failed</span></label>
        </div>

        <table id="testTable" class="display" style="width:100%">
        <thead>
            <tr>
            <th>Section</th>
            <th>Tag</th>
            <th>Result</th>
            <th>Duration (s)</th>
            </tr>
        </thead>
        <tbody>
            {table_rows}
        </tbody>
        </table>
    </div>

    <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
    <script src="https://cdn.datatables.net/1.11.5/js/jquery.dataTables.min.js"></script>
    <script>
        $(document).ready(function () {{
        var table = $('#testTable').DataTable({{
            responsive: true,
            dom: '<"top"lf>rt<"bottom"ip>',
            language: {{
            search: "_INPUT_",
            searchPlaceholder: "Search tests...",
            lengthMenu: "Show _MENU_ tests per page",
            info: "Showing _START_ to _END_ of _TOTAL_ tests",
            infoEmpty: "No tests available",
            infoFiltered: "(filtered from _MAX_ total tests)"
            }}
        }});

        const detailsData = {{
            {js_details}
        }};

        function format(tag) {{
            const data = detailsData[tag];
            return `
            <div class="details-content">
                <div><strong>Test Name:</strong> ${{data.name}}</div>
                <div><strong>Class:</strong> ${{data.classname}}</div>
                <div><strong>Status:</strong> ${{data.failure ? '<span class="failed">Failed</span>' : '<span class="passed">Passed</span>'}}</div>
                ${{data.failure ? `<div><strong>Failure Reason:</strong><br><pre>${{data.failure}}</pre></div>` : ''}}
                <div><strong>System Output:</strong><br><pre>${{data.system_out.join("\\n\\n")}}</pre></div>
                <div><strong>Terminate:</strong> ${{data.terminate}}</div>
                <div><strong>Feature File:</strong> ${{data.feature_file}}</div>
                <div><strong>Output File:</strong> ${{data.output_file}}</div>
                <div><strong>Proofs:</strong> ${{data.proofs_video}}</div>
                <div><strong>Proofs Folder:</strong> ${{data.proofs_base_folder_includes_screenshots_recording_netwrok_logs_api_logs_sec_logs_accessibility_logs}}</div>
                <div><strong>Screenshots:</strong> ${{data.proofs_screenshot}}</div>
                <div><strong>Network Logs:</strong> ${{data.network_logs}}</div>
                <div><strong>Internal Logs:</strong> ${{data.agents_internal_logs}}</div>
                <div><strong>Plan:</strong><br><pre>${{data.plan}}</pre></div>
                ${{data.next_step ? `<div><strong>Next Step:</strong><br><pre>${{data.next_step}}</pre></div>` : ''}}
            </div>
            `;
        }}

        $('#testTable tbody').on('click', 'td.details-control', function () {{
            const tr = $(this).closest('tr');
            const row = table.row(tr);
            const tag = row.data()[1];

            if (row.child.isShown()) {{
            row.child.hide();
            tr.removeClass('shown');
            }} else {{
            row.child(format(tag)).show();
            tr.addClass('shown');
            }}
        }});

        // Checkbox filter
        $('.result-filter').on('change', function () {{
            var selected = $('.result-filter:checked').map(function() {{
            return this.value;
            }}).get().join('|'); // regex OR

            table.column(2).search(selected, true, false).draw();
        }});
        }});
    </script>

    </body>
    </html>
    """

    async def write_html(self, html_file_path: str) -> None:
        with open(html_file_path, "w") as f:
            f.write(self.html_content)


async def generate_html_report(xml_file_path: str, html_file_path: str) -> str:
    generater = HTMLGenerator()
    test_data = generater.parse_junit_xml(xml_file_path)
    generater.build_html(test_data)
    await generater.write_html(html_file_path)
    return html_file_path