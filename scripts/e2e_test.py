#!/usr/bin/env python3
"""End-to-end test script: creates student, thesis, runs analysis, generates report."""
import time
import requests

API = 'http://127.0.0.1:8000/api'

def main():
    # create student
    s = requests.post(f'{API}/students/', json={
        'full_name': 'Test Student', 'student_id': 'S123', 'email': 'a@b.com'
    }).json()
    print('student', s)

    # create thesis manually
    data = {
        'title': 'E2E Test Thesis',
        'student_id': s.get('id'),
    }
    # /api/theses/manual expects JSON body with data and content
    resp = requests.post(f'{API}/theses/manual', json={'data': data, 'content': 'Ky është një tekst test për analizë.'})
    print('create thesis', resp.status_code, resp.text[:200])
    tid = resp.json().get('thesis_id')

    # start analysis
    r = requests.post(f'{API}/analysis/start', json={'thesis_id': tid, 'compare_internal': True, 'search_web': False})
    print('start analysis', r.status_code, r.text)
    result_id = r.json().get('result_id')

    # poll
    for _ in range(30):
        time.sleep(1)
        st = requests.get(f'{API}/analysis/result/{result_id}').json()
        print('status', st.get('status'), 'overall', st.get('overall_score'))
        if st.get('status') == 'completed':
            break

    # generate report
    rep = requests.post(f'{API}/reports/generate', json={'result_id': result_id}).json()
    print('report preview:', rep.get('report')[:300])

if __name__ == '__main__':
    main()
