terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.40"
    }
  }
}

variable "alarm_topic_arn" {
  description = "SNS topic paging the on-call rotation."
  type        = string
}

variable "target_group_arn_suffix" {
  description = "ALB target group dimension, for example targetgroup/gitlab/0123456789abcdef."
  type        = string
}

variable "load_balancer_arn_suffix" {
  description = "ALB dimension, for example app/gitlab/0123456789abcdef."
  type        = string
}

variable "db_instance_id" {
  description = "RDS instance backing GitLab."
  type        = string
}

variable "canary_name" {
  description = "Synthetics canary running the git clone check."
  type        = string
}

variable "sidekiq_queue_threshold" {
  description = "Sidekiq backlog that counts as degraded. This moves before users notice."
  type        = number
  default     = 500
}

variable "tags" {
  description = "Tags applied to every alarm."
  type        = map(string)
  default     = {}
}

resource "aws_cloudwatch_metric_alarm" "clone_failing" {
  alarm_name          = "gitlab-git-clone-failing"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  threshold           = 100
  alarm_description   = "Synthetics canary could not clone a repository"
  alarm_actions       = [var.alarm_topic_arn]
  ok_actions          = [var.alarm_topic_arn]
  treat_missing_data  = "breaching"
  tags                = var.tags

  metric_name = "SuccessPercent"
  namespace   = "CloudWatchSynthetics"
  period      = 300
  statistic   = "Average"

  dimensions = {
    CanaryName = var.canary_name
  }
}

resource "aws_cloudwatch_metric_alarm" "no_healthy_hosts" {
  alarm_name          = "gitlab-no-healthy-hosts"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  threshold           = 1
  alarm_description   = "No healthy targets behind the load balancer"
  alarm_actions       = [var.alarm_topic_arn]
  treat_missing_data  = "breaching"
  tags                = var.tags

  metric_name = "HealthyHostCount"
  namespace   = "AWS/ApplicationELB"
  period      = 60
  statistic   = "Minimum"

  dimensions = {
    TargetGroup  = var.target_group_arn_suffix
    LoadBalancer = var.load_balancer_arn_suffix
  }
}

resource "aws_cloudwatch_metric_alarm" "sidekiq_backlog" {
  alarm_name          = "gitlab-sidekiq-backlog"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = var.sidekiq_queue_threshold
  alarm_description   = "Background job backlog growing, the earliest signal of degradation"
  alarm_actions       = [var.alarm_topic_arn]
  treat_missing_data  = "notBreaching"
  tags                = var.tags

  metric_name = "SidekiqQueueSize"
  namespace   = "GitLab"
  period      = 300
  statistic   = "Maximum"
}

resource "aws_cloudwatch_metric_alarm" "repo_disk_filling" {
  alarm_name          = "gitlab-repository-disk-low"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  threshold           = 15
  alarm_description   = "Free space on the repository volume, a routine cause of full outages"
  alarm_actions       = [var.alarm_topic_arn]
  treat_missing_data  = "breaching"
  tags                = var.tags

  metric_name = "disk_free_percent"
  namespace   = "CWAgent"
  period      = 300
  statistic   = "Minimum"
}

resource "aws_cloudwatch_metric_alarm" "database_connections" {
  alarm_name          = "gitlab-db-connections-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = 80
  alarm_description   = "Connection pool close to exhaustion"
  alarm_actions       = [var.alarm_topic_arn]
  treat_missing_data  = "notBreaching"
  tags                = var.tags

  metric_name = "DatabaseConnections"
  namespace   = "AWS/RDS"
  period      = 300
  statistic   = "Maximum"

  dimensions = {
    DBInstanceIdentifier = var.db_instance_id
  }
}

# Pages only when the clone check fails and the fleet is unhealthy, which keeps a single failed probe quiet.
resource "aws_cloudwatch_composite_alarm" "service_down" {
  alarm_name        = "gitlab-service-down"
  alarm_description = "Git is unusable and the fleet is not serving"
  alarm_actions     = [var.alarm_topic_arn]
  tags              = var.tags

  alarm_rule = join(" AND ", [
    "ALARM(${aws_cloudwatch_metric_alarm.clone_failing.alarm_name})",
    "ALARM(${aws_cloudwatch_metric_alarm.no_healthy_hosts.alarm_name})",
  ])
}

output "alarm_names" {
  description = "Alarms created, for wiring into a dashboard."
  value = [
    aws_cloudwatch_metric_alarm.clone_failing.alarm_name,
    aws_cloudwatch_metric_alarm.no_healthy_hosts.alarm_name,
    aws_cloudwatch_metric_alarm.sidekiq_backlog.alarm_name,
    aws_cloudwatch_metric_alarm.repo_disk_filling.alarm_name,
    aws_cloudwatch_metric_alarm.database_connections.alarm_name,
    aws_cloudwatch_composite_alarm.service_down.alarm_name,
  ]
}
