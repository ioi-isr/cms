# CMS Local Testing

## Running the App Locally

1. Start Docker environment:
   ```bash
   cd /home/ubuntu/repos/cms
   docker compose -f docker/docker-compose.yml -p cms-training-program up -d
   ```

2. Enter the dev container:
   ```bash
   docker exec -it cms-training-program-cms-1 bash
   ```

3. Initialize database (inside container):
   ```bash
   createdb -h devdb -U postgres cmsdb
   cmsInitDB
   cmsAddAdmin -p admin admin
   ```

4. Import test contest:
   ```bash
   git clone https://github.com/cms-dev/con_test.git
   cd con_test
   cmsImportUser --all
   cmsImportContest -i .
   ```

5. Start services:
   ```bash
   cmsResourceService -a ALL
   ```

## Key URLs

- AdminWebServer: http://localhost:8889
- Login: admin / admin
- Task page: http://localhost:8889/task/{task_id}
- Subtask details: http://localhost:8889/dataset/{dataset_id}/subtask/{subtask_idx}/details

## Database Reset

If the schema is outdated (e.g., after branch changes with new tables), reset from scratch:
```bash
# Inside the container
dropdb -h devdb -U postgres cmsdb
createdb -h devdb -U postgres cmsdb
cmsInitDB
```

## Testing Notes

- The con_test contest has 9 tasks; task 1 ("batch") has Hebrew statements and GroupMin subtasks with 10 testcases
- Always clear browser cache when testing CSS/template changes
- The `fixed-grid has-2-cols` layout doesn't fully collapse on very narrow mobile viewports (~375px) - this is expected behavior for the admin interface
- CSS class `.tp-filter-card` has `display: flex` which overrides the HTML `hidden` attribute; use `style="display: none;"` instead when toggling visibility
