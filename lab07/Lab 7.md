# Lab 06: Weather Advisor in the Cloud: Two Tiers, a Free AI Model and a PostgreSQL Database  
# Name: Vishnu Upadhya  

---
## Output of terraform version and aws sts get-caller-identity (hide the account number)

![terraform version and aws sts get-caller-identity](images/image1.png)

Terraform v1.17.1 is installed on linux_amd64, which is newer than the required 1.5.0, and aws sts get-caller-identity returned the lab-user identity, so Terraform can use the same AWS credentials as the AWS CLI. The account number is hidden.

## Part A: the plan before the first apply, the second apply showing No changes, the in-place update plan (~) and the replacement plan (-/+), with a sentence on why -/+ is the one to be careful about

The plan before the first apply:

![terraform init and the first plan](images/image2.png)

![End of the first plan, 1 to add](images/image3.png)

The apply, the describe-table check (ACTIVE), terraform state list, and the second apply showing No changes:

![Apply complete, then the second apply with No changes](images/image4.png)

The in-place update plan after adding the Purpose tag:

![In-place update plan](images/image5.png)

The replacement plan:

> **Screenshot to add:** the `-/+` plan with the note that `hash_key` forces replacement (Part A3).

The first plan said Plan: 1 to add, 0 to change, 0 to destroy, and listed the table attributes as (known after apply). The second apply printed No changes, which is idempotence: the same configuration was applied twice and nothing was done the second time, without a single `if` being written. Adding the tags line produced a ~ update in place with + "Purpose" = "first-taste", and Plan: 0 to add, 1 to change, 0 to destroy. The -/+ plan is the one to be careful about because DynamoDB cannot change a table's key, so changing the hash_key makes Terraform destroy the table and create a new empty one, which deletes all of the data in it, and the plan is the only warning given before that happens.

## Output of terraform fmt, terraform validate and the last lines of terraform plan showing the number of resources to add

![terraform fmt, terraform validate and terraform plan -out tfplan](images/image6.png)

> **Screenshot to add:** the last lines of `terraform plan -out tfplan`, showing `Plan: 25 to add, 0 to change, 0 to destroy.`

terraform fmt printed nothing, which means the files were already formatted, and terraform validate printed Success! The configuration is valid. The plan reads the default VPC, the subnets and the newest Amazon Linux 2023 image as data sources and then lists the resources to create: the load balancer, two target groups, two listeners, two launch templates, two Auto Scaling Groups, two roles and instance profiles, two policies, one table, one parameter, and the security groups with their rules. The count of 25 matches the 25 resources later removed by terraform destroy.

## Output of app_smoke_test.py with all nine checks passing, and a screenshot of the advisor page

![app_smoke_test.py, 9 of 9 checks passed](images/image7.png)

![Advisor page for zip 60616](images/image8.png)

All nine checks passed: the weather tier returned a forecast, the repeat call was a cache hit answered from DynamoDB, /history listed the saved lookup, a bad zip gave 400, an unknown zip gave 404, the advisor returned advice from Gemini (source=gemini, model gemini-3.1-flash-lite, in 2.3 seconds), a bad zip on the advisor gave 400, and the advisor home page loaded. The advisor page for zip 60616 shows Chicago, IL, the forecast for this afternoon, the advice, and Suggestion source: gemini. The page was opened through the load balancer on port 8081, and the addresses for the smoke test came from terraform output, so no file had to be kept in sync.

## Steps 1 to 3: the effort table with line counts, the terraform graph output with a sentence, and the No changes plan with a sentence comparing it to running the script twice

### Step 1. Count the effort

![Line counts](images/image9.png)

| | lab06 Python | lab07 Terraform |
|---|---|---|
| Lines to **create** the stack | 101 (173 lines in total minus the 72 delete lines) | 432 |
| Lines to **delete** the stack | 72 | 0 |
| Steps you had to order by hand | 7 | 0 |
| Helper functions you wrote | 7 (the `ensure_` helpers) | 0 |
| Retry or wait loops you wrote | 4 | 0 |

The lab06 scripts have 173 lines in total (129 without blanks and comments), and the Terraform files have 432 lines in total (346 without blanks and comments), so the Terraform was not shorter. The seven steps ordered by hand in lab06 were the check of the Gemini key, the IAM roles, the VPC and subnets, the security groups, the target groups with the load balancer and listeners, the launch templates, and the Auto Scaling Groups. The seven helpers are ensure_role_and_profile, ensure_alb_security_group, ensure_target_group, ensure_load_balancer, ensure_listener, ensure_launch_template and ensure_asg, and the four waits and retries are the IAM propagation sleep, the Auto Scaling Group delete wait, the ResourceInUse retry for the target groups, and the security group retry. Terraform needed none of these.

The Terraform was not shorter than the lab06 scripts, since 432 lines is more than 173, so the saving was in kinds of work rather than in lines. No delete script, no ordering of steps, no ensure helpers and no retry or wait loops had to be written, because Terraform compares the files with its state and works out the order itself.

### Step 2. Prove the order Terraform worked out

![terraform state list and terraform graph](images/image10.png)

The graph shows the arrow "aws_launch_template.tier" -> "aws_lb.main", so the launch templates depend on the load balancer, together with "aws_lb_listener.tier" -> "aws_lb.main" and "aws_autoscaling_group.tier" -> "aws_launch_template.tier". If the WEATHER_URL line were removed from compute.tf, the advisor's launch template would no longer refer to aws_lb.main, so Terraform would no longer have to create the load balancer first, and the advisor would not work because it would not know the address of the weather tier.

### Step 3. Run apply again

![terraform plan, No changes](images/image11.png)

The plan says No changes. Your infrastructure matches the configuration. Running the lab06 create script twice only printed "already exists, skipping" for each name it found, so it checked whether a resource with that name existed but not whether its settings were still correct, while terraform plan compares every setting of every resource with the code and the state.

## Steps 4 and 5: the plan for cache_seconds = 60, the instance refresh status and new CACHE_SECONDS=60 line, and the drift plans with a sentence about ensure_target_group

### Step 4. Change one setting and watch a rolling replacement

The plan after adding cache_seconds = 60 to terraform.tfvars:

![Plan for cache_seconds = 60, first part](images/image12.png)

![Plan for cache_seconds = 60, second part](images/image13.png)

![Plan for cache_seconds = 60, summary](images/image14.png)

The instance refresh status and the new setting on the new instance:

![Instance refresh status and CACHE_SECONDS=60](images/image15.png)

The plan shows only the weather tier changing: its Auto Scaling Group (~, an update in place that starts an instance refresh) and its launch template (~, a new version, because the user-data changed). It ends with Plan: 0 to add, 2 to change, 0 to destroy. The advisor, the load balancer, the table and the roles are not touched, because nothing they depend on changed. After the apply, the instance refresh reached 100 percent and the new instance showed Environment="CACHE_SECONDS=60". This took 2 commands in lab07 (edit terraform.tfvars and run terraform apply, with an optional terraform plan to preview), compared with 4 in lab06 (edit .env, run the create script, find the instance, and terminate it).

### Step 5. Cause drift and watch Terraform notice

The target group health check path changed by hand to /nope:

![modify-target-group to /nope](images/image16.png)

The drift plan:

![Drift plan, /nope back to /](images/image17.png)

> **Screenshot to add (if available):** the plan with `1 to add` after the advisor's listener was deleted.

The describe-target-groups output shows HealthCheckPath "/nope" after the change by hand, and the plan shows ~ path = "/nope" -> "/" with Plan: 0 to add, 1 to change, 0 to destroy, so Terraform noticed the drift without being told. In lab06, ensure_target_group found the group already existing and printed "skipping" without checking that its health check was still correct, whereas Terraform noticed that the health check path had been changed and planned to set it back to /, so it repairs drift that the lab06 script would never have seen.

## Steps 6 and 7: the IAM simulator results with a sentence about the advisor tier, and the No changes plan, the grep -c count of 0 and the sentences about ignore_changes

### Step 6. Who can do what? (least privilege, proved)

![IAM simulator results](images/image18.png)

The four results were allowed for the weather role with dynamodb:PutItem, implicitDeny for the weather role with ssm:GetParameter, allowed for the advisor role with ssm:GetParameter, and implicitDeny for the advisor role with dynamodb:GetItem. (In the screenshot the result words overwrite the start of each role name because of how the terminal printed them, but the four results are in the expected order.) An attacker who takes over the advisor tier gets only the ability to read the one Gemini key parameter, and does not get any access to the weather table or to any other resource, although that key would still have to be replaced.

### Step 7. The key never touched Terraform

![No changes plan and the grep checks](images/image19.png)

The plan says No changes even though the value stored in Parameter Store is different from not-set-yet, which shows that ignore_changes = [value] makes Terraform leave the value alone, and the grep -c count of 0 shows that no text starting with AIza is in the state file. The second grep for not-set-yet printed no lines in this run, so the placeholder was not found in the state either (the lab text expects to see it there). Without ignore_changes, the next terraform apply would overwrite the stored Gemini key with the placeholder not-set-yet, so the advisor would silently fall back to its rule-based answers, and the state would then hold the value that Terraform manages.

## Steps 8 and 9: the fallback answers with the gemini_error journal line and a sentence about silent fallback, and the completed Lambda table with the answer to question 4

### Step 8. Break the key and watch the fallback

![Key overwritten, fallback answers, gemini_error journal lines](images/image20.png)

After the key was overwritten with not-a-real-key (Parameter Store returned Version 4) and the key cache expired, both answers came back with source fallback ("Wear a light jacket or a sweater over long sleeves."), and no request returned an error. The journal lines at 23:30:33 and 23:30:34 show "event":"gemini_error","status":400, which is Google rejecting the invalid key. The weakness of a design where users never see an error is that a broken or expired key can go unnoticed for days, because every request still returns a normal-looking answer and the only evidence is a gemini_error log line that nobody reads, so an alarm or a metric on the rate of fallback answers would be needed.

### Step 9. The Lambda exercise (no resources, written answer)

The resources that exist only because the advisor runs on an EC2 instance are, in compute.tf, the launch template with its user-data and the Auto Scaling Group with its instance refresh; in iam.tf, the advisor's instance profile (the role stays, as an execution role); in network.tf, the app security group with its rule for port 3000 from the load balancer, its outbound rule, its SSH rule, and the AMI lookup; and in alb.tf, the health check settings of the target group. What would stay is the SSM parameter and the advisor's read-one-secret policy, the load balancer with its security group, the listener on port 8081, the target group (with a different target type), the role (as an execution role), the outputs and the tags. What would be new is an aws_lambda_function resource with its code package, a Lambda permission that lets the load balancer invoke the function, a target group with target type lambda, and the work of changing the Node.js code from an Express server to a handler for the load balancer's Lambda event.

| Advisor on EC2 (this lab) | Advisor on Lambda | Effort moved to |
|---|---|---|
| Launch template and user-data | Replaced by an aws_lambda_function resource and its code package | AWS runs the servers; the code package is still managed by the developer |
| Auto Scaling Group and instance refresh | Not needed: Lambda scales per request, and publishing a new code version replaces the old one | AWS |
| App security group rule for port 3000 | Not needed for this tier: the load balancer needs a Lambda permission to invoke the function | An IAM permission instead of a network rule |
| Instance profile and role | Replaced by a Lambda execution role with the same read-one-secret policy | Still the developer: least privilege still applies |
| Node.js install and systemd service | Not needed: the Lambda runtime provides Node.js, but Express is replaced by an event handler | AWS runtime, but the application code has to be rewritten for the event format |
| Health check and grace period | Not needed: there are no instances that can be unhealthy, and the target group uses a Lambda target | AWS; failures show up as invocation errors instead |

Answer to question 4: Terraform removed the effort of creating the resources in the right order and checking whether they already exist, while Lambda would remove the effort of running servers, patching the operating system and scaling, so Lambda removes most of the first list (the EC2-only resources) and Terraform removes the ordering work for the other two lists. What is gained is less machinery to describe and get wrong, and the new problems taken on are cold starts that add delay to the first request, the time limit of a function, the need to rewrite the code for a Lambda event, and the harder testing of a handler compared with a normal web server.

## Short answers: how Terraform is like AWS Lambda and where the comparison stops, what Terraform did not make easier, and the answers to the Section 13.1 review questions

### How Terraform is like AWS Lambda, and where the comparison stops

Terraform is like AWS Lambda in that in both cases a small, self-contained piece (a configuration or a function) is handed over and the platform does the repetitive, error-prone work around it: Terraform works out the order, waits and retries, and compares before and after, and Lambda handles servers, scaling and health checks. Both start only when they are asked to and stop when they finish, both are idempotent when written well (the second apply changed nothing), both act through an IAM role with least privilege, and in both the code is the record of what exists.

The comparison stops because Lambda is a runtime service that AWS runs, which executes the application code on each request and keeps no state between invocations, while Terraform is a deployment tool that runs on the developer's machine before the application exists, never handles a request, keeps a state file whose loss is a serious problem, and is free itself while the resources it creates cost money. The difference that matters most for how each would be used is that Lambda is where the application logic runs, and Terraform is how the infrastructure (which can include a Lambda function) is described and created.

### What Terraform did not make easier

Terraform was not shorter: the 432 lines of Terraform are more than the 173 lines of lab06 Python, so the saving was in kinds of work, not in lines. Storing the real Gemini key stayed a one-time imperative CLI command, and the key had to be kept out of the files and the state on purpose with ignore_changes. Debugging the applications still needed SSH and the journal (for example the gemini_error lines), because Terraform only describes the infrastructure and cannot tell whether the code works, and the Gemini problems (timeouts, overload and a wrong key) were the same as in lab06. A new language (HCL), a workflow (init, plan, apply, destroy) and a state file to protect also had to be learned, and the destroy took about 6 minutes because the Auto Scaling Groups wait for their instances to terminate. Cleanup also could not be proved from Terraform alone, since the tagging API still listed stale entries after the destroy (see the last item).

### Answers to the Section 13.1 review questions

| Topic | Answer |
|---|---|
| Declarative and imperative | The imperative lab06 version could run any custom or one-off action, such as storing the real Gemini key, which is awkward to express in Terraform. Terraform compared the files with its state and with AWS and showed a plan before changing anything, which the lab06 scripts never did, and it did not need a delete script because it removed everything it had created. |
| State file | If the state file were lost, Terraform would no longer know what it built, so it would try to create everything again and the old resources would be left behind, costing money. If two people applied from different copies, each would work from a different view of reality and could overwrite or duplicate resources. Teams keep the state in a shared, locked place such as an S3 bucket, so that only one person can change it at a time. |
| Secrets in state | A secret could also reach the state or a plan file through a variable value, a resource attribute, or a data source that reads it. A plan file is sensitive because it holds the same planned values, including any secret, in readable form. |
| Plan before apply | Reading the plan protects against unintended changes and deletions, because everything that will be created, updated or destroyed is shown before it happens. The -/+ symbol worries the most, because it means destroy and create again, which can lose data, as the Part A table replacement showed. |
| Version pinning | Without a pin, a new provider version could change behavior or break the code without any edit. The .terraform.lock.hcl file is committed so that everyone gets exactly the same provider version (5.100.0 here). |
| for_each or count | for_each names each copy by its key (for example tier["weather"]), so removing one tier removes only that one. count uses a number, so removing an item from the middle shifts the others and causes needless replacement. |
| Ownership | When a tool and a person both edit the same resource, the tool undoes the person's change (as the /nope health check was set back to /), or the two keep fighting, and the state stops matching reality. |
| Drift | A team could run terraform plan on a schedule in a pipeline and alert on any difference. Only the pipeline (or a small group) should be allowed to change things by hand, and only in emergencies, after which the code is updated. |
| Blast radius | Changing cache_seconds touched only the weather tier because nothing else depended on it, so what a change touches is decided by what depends on it. It can be shrunk further by splitting the stack into smaller separate configurations or modules. |
| Least privilege | A reference always points to the real resource and follows it when it changes, while a typed-in ARN can contain a typo or point to the wrong resource, as happened with the AccessDenied in lab06. |
| Terraform and Lambda | Both take a small self-contained piece and let the platform do the repetitive work. The difference that matters most is that Lambda is a runtime that runs the code per request, while Terraform is a deployment tool that runs before the application exists and keeps state. |
| Destroy | Resources that hold data or secrets should be protected from a careless destroy: a table with data, a Parameter Store secret and the state storage. lifecycle { prevent_destroy = true } makes the destroy fail for those. |

## Step 10: the terraform destroy plan and total time, the cost estimate (hours multiplied by the hourly cost), and the verification commands showing nothing remains

The time before the destroy and the start of the plan:

![date and the start of terraform destroy](images/image21.png)

The destroy plan:

![Destroy plan, advisor Auto Scaling Group](images/image22.png)

![Destroy plan, 25 to destroy and the confirmation](images/image23.png)

The destroy finishing:

![Destroy complete, 25 destroyed](images/image24.png)

The verification commands:

![Verification commands](images/image25.png)

The date before the destroy was Sun Nov 8 23:32:21 UTC 2026. The plan said Plan: 0 to add, 0 to change, 25 to destroy, and after typing yes the destroy finished with Destroy complete! Resources: 25 destroyed. The Auto Scaling Groups took 5m42s (advisor) and 6m02s (weather) to destroy and the load balancer took 36s, so the whole destroy took about 7 minutes. This one command replaced the lab06 delete scripts (72 lines), their retry loops for the security group, and the separate delete for the database.

The stack was applied at about 22:55 UTC and the destroy finished at about 23:39 UTC, so it existed for about 44 minutes, or about 0.73 hours. At the hourly cost of about $0.07, the estimate is 0.73 hours x $0.07 = about $0.05, far below the $1.00 budget.

The verification shows that terraform state list printed nothing, and the commands for load balancers, Auto Scaling Groups, DynamoDB tables, Parameter Store parameters and the itmo-544 roles all returned empty. The describe-security-groups command with the filter itmo-544-lab07* also returned nothing, which shows that both security groups are gone. The tagging command still listed six security group rules, but rules cannot exist without their security groups, so these are stale entries in the tagging index, which can take a long time to update. The two instances named viewer-app belong to an earlier lab, have no Project=itmo-544-lab07 tag, and were not created by this lab's Terraform. The Gemini key must still be deleted in Google AI Studio, since the parameter was only a copy, and Billing and Cost Management should be checked in a few hours.
